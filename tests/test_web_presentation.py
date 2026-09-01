from __future__ import annotations

import base64
import json
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from web_presentation import (
    AUTHKEY_ENVIRONMENT_VARIABLE,
    CHILD_MODE_ARGUMENT,
    PIPE_ARGUMENT,
    PIPE_FAMILY,
    InvalidPresentationMessage,
    MAX_MESSAGE_DEPTH,
    PresentationSnapshot,
    PresentationState,
    WebPresentationController,
    build_presentation_child_command,
    read_presentation_authkey,
    validate_request,
)


class ChildCommandTests(unittest.TestCase):
    def test_source_command_keeps_authkey_out_of_arguments(self) -> None:
        command = build_presentation_child_command(
            "web_child.py",
            r"\\.\pipe\test-presentation",
            b"a" * 32,
            executable="pythonw.exe",
            packaged=False,
        )

        self.assertEqual(command.argv[0], "pythonw.exe")
        self.assertEqual(command.argv[1], "-c")
        self.assertEqual(Path(command.argv[3]).name, "web_child.py")
        self.assertEqual(command.argv[4], r"\\.\pipe\test-presentation")
        self.assertNotIn(CHILD_MODE_ARGUMENT, command.argv)
        encoded_key = base64.urlsafe_b64encode(b"a" * 32).decode("ascii").rstrip("=")
        self.assertNotIn(encoded_key, command.argv)
        self.assertEqual(command.environment[AUTHKEY_ENVIRONMENT_VARIABLE], encoded_key)
        self.assertEqual(read_presentation_authkey(command.environment), b"a" * 32)

    def test_packaged_command_reuses_executable_without_source_path(self) -> None:
        command = build_presentation_child_command(
            "ignored.py",
            r"\\.\pipe\test-presentation",
            b"b" * 32,
            executable="FenSoundSwitch.exe",
            packaged=True,
        )

        self.assertEqual(
            command.argv,
            ("FenSoundSwitch.exe", CHILD_MODE_ARGUMENT, PIPE_ARGUMENT, r"\\.\pipe\test-presentation"),
        )


class MessageValidationTests(unittest.TestCase):
    def test_rejects_extra_fields_and_non_object_params(self) -> None:
        with self.assertRaises(InvalidPresentationMessage):
            validate_request({"id": 1, "method": "ready", "extra": True})
        with self.assertRaises(InvalidPresentationMessage):
            validate_request({"id": 1, "method": "ready", "params": []})

    def test_rejects_non_json_and_excessive_nesting(self) -> None:
        with self.assertRaises(InvalidPresentationMessage):
            validate_request({"id": 1, "method": "ready", "params": {"bad": object()}})
        nested: object = None
        for _ in range(MAX_MESSAGE_DEPTH + 2):
            nested = [nested]
        with self.assertRaises(InvalidPresentationMessage):
            validate_request({"id": 1, "method": "ready", "params": {"nested": nested}})


class ControllerRequestTests(unittest.TestCase):
    def make_controller(self, *, queued: bool = False) -> tuple[WebPresentationController, list[object], Mock]:
        callbacks: list[object] = []
        dispatcher = Mock(return_value={"accepted": True})
        post_to_ui = callbacks.append if queued else lambda callback: callback()
        controller = WebPresentationController(
            "web_child.py",
            post_to_ui=post_to_ui,
            get_snapshot=lambda: PresentationSnapshot(7, {"volume": 42}),
            dispatch_action=dispatcher,
            allowed_actions={"set-volume"},
            on_exit=lambda: callbacks.append("exit"),
            on_minimize=lambda: callbacks.append("minimize"),
            on_visibility=lambda visible: callbacks.append(("visible", visible)),
            on_tray_recovery=lambda: callbacks.append("tray"),
            request_timeout=0.05,
        )
        controller._state = PresentationState.CONNECTED
        return controller, callbacks, dispatcher

    @staticmethod
    def request(controller: WebPresentationController, method: str, params: object = None) -> dict[str, object]:
        message = {"id": 9, "method": method}
        if params is not None:
            message["params"] = params
        return controller._handle_payload(json.dumps(message).encode("utf-8"))

    def test_ready_and_revision_aware_snapshot_state_machine(self) -> None:
        controller, _, _ = self.make_controller()

        ready = self.request(controller, "ready")
        unchanged = self.request(controller, "get_snapshot", {"revision": 7})

        self.assertEqual(controller.state, PresentationState.READY)
        self.assertEqual(ready["result"], {"revision": 7, "changed": True, "snapshot": {"volume": 42}})
        self.assertEqual(unchanged["result"], {"revision": 7, "changed": False})

    def test_unknown_method_and_action_are_rejected(self) -> None:
        controller, _, dispatcher = self.make_controller()
        self.request(controller, "ready")

        unknown_method = self.request(controller, "open_shell")
        unknown_action = self.request(controller, "dispatch_action", {"action": "delete-everything"})

        self.assertFalse(unknown_method["ok"])
        self.assertFalse(unknown_action["ok"])
        self.assertEqual(unknown_method["error"]["code"], "invalid_request")
        dispatcher.assert_not_called()

    def test_actions_and_presentation_callbacks_are_dispatched_on_ui(self) -> None:
        controller, callbacks, dispatcher = self.make_controller()
        self.request(controller, "ready")

        action = self.request(
            controller,
            "dispatch_action",
            {"action": "set-volume", "arguments": {"volume": 33}},
        )
        self.request(controller, "show")
        self.request(controller, "minimize")
        self.request(controller, "recover_tray")
        self.request(controller, "close")

        self.assertEqual(action["result"], {"accepted": True})
        dispatcher.assert_called_once_with("set-volume", {"volume": 33})
        self.assertEqual(callbacks, [("visible", True), ("visible", True), "minimize", "tray", ("visible", False), "exit"])
        self.assertEqual(controller.state, PresentationState.CLOSING)

    def test_reader_side_waits_for_queued_ui_callback(self) -> None:
        controller, callbacks, _ = self.make_controller(queued=True)
        response: list[dict[str, object]] = []
        worker = threading.Thread(target=lambda: response.append(self.request(controller, "ready")))
        worker.start()
        while not callbacks:
            threading.Event().wait(0.005)

        self.assertTrue(worker.is_alive())
        queued_callback = callbacks.pop()
        self.assertTrue(callable(queued_callback))
        queued_callback()
        worker.join(0.5)

        self.assertFalse(worker.is_alive())
        self.assertTrue(response[0]["ok"])

    def test_ui_queue_timeout_does_not_run_dispatcher_on_reader_thread(self) -> None:
        controller, callbacks, dispatcher = self.make_controller(queued=True)

        response = self.request(controller, "ready")

        self.assertFalse(response["ok"])
        dispatcher.assert_not_called()
        callbacks[0]()
        self.assertEqual(controller.state, PresentationState.CONNECTED)


class ControllerLifecycleTests(unittest.TestCase):
    def make_controller(self, listener_factory: Mock, popen_factory: Mock, callbacks: list[object]) -> WebPresentationController:
        return WebPresentationController(
            "web_child.py",
            post_to_ui=lambda callback: callbacks.append(callback),
            get_snapshot=lambda: (1, {}),
            dispatch_action=Mock(),
            allowed_actions=set(),
            on_exit=Mock(),
            on_minimize=Mock(),
            on_visibility=Mock(),
            on_tray_recovery=lambda: callbacks.append("tray"),
            on_child_failure=lambda reason: callbacks.append(reason),
            listener_factory=listener_factory,
            popen_factory=popen_factory,
            executable="pythonw.exe",
            packaged=False,
            shutdown_timeout=0.05,
        )

    def test_launch_uses_authenticated_af_pipe_and_secret_only_in_environment(self) -> None:
        listener = Mock()
        listener.accept.side_effect = OSError("stop")
        process = Mock()
        process.wait.return_value = 0
        listener_factory = Mock(return_value=listener)
        popen_factory = Mock(return_value=process)
        callbacks: list[object] = []
        controller = self.make_controller(listener_factory, popen_factory, callbacks)

        self.assertTrue(controller.launch())
        controller.shutdown()

        listener_kwargs = listener_factory.call_args.kwargs
        self.assertEqual(listener_kwargs["family"], PIPE_FAMILY)
        self.assertTrue(listener_kwargs["address"].startswith(r"\\.\pipe\fensoundswitch-presentation-"))
        self.assertEqual(len(listener_kwargs["authkey"]), 32)
        popen_args = popen_factory.call_args.args[0]
        popen_env = popen_factory.call_args.kwargs["env"]
        encoded_key = base64.urlsafe_b64encode(listener_kwargs["authkey"]).decode("ascii").rstrip("=")
        self.assertNotIn(encoded_key, popen_args)
        self.assertEqual(popen_env[AUTHKEY_ENVIRONMENT_VARIABLE], encoded_key)

    def test_launch_failure_is_retryable_and_closes_listener(self) -> None:
        first_listener = Mock()
        second_listener = Mock()
        second_listener.accept.side_effect = OSError("stop")
        process = Mock()
        process.wait.return_value = 0
        listener_factory = Mock(side_effect=[first_listener, second_listener])
        popen_factory = Mock(side_effect=[OSError("missing child"), process])
        controller = self.make_controller(listener_factory, popen_factory, [])

        self.assertFalse(controller.launch())
        self.assertEqual(controller.state, PresentationState.FAILED)
        first_listener.close.assert_called_once_with()
        self.assertTrue(controller.launch())
        controller.shutdown()

        self.assertEqual(listener_factory.call_count, 2)
        self.assertEqual(popen_factory.call_count, 2)

    def test_unexpected_child_exit_posts_tray_recovery_once(self) -> None:
        listener = Mock()
        process = Mock()
        process.wait.return_value = 23
        controller_callbacks: list[object] = []
        controller = self.make_controller(Mock(return_value=listener), Mock(return_value=process), controller_callbacks)
        listener_closed = threading.Event()
        listener.close.side_effect = listener_closed.set

        def accept_until_closed() -> None:
            listener_closed.wait(0.5)
            raise OSError("closed")

        listener.accept.side_effect = accept_until_closed

        controller.launch()
        controller._process_thread.join(0.5)
        ui_callbacks = [item for item in controller_callbacks if callable(item)]
        self.assertEqual(len(ui_callbacks), 1)
        ui_callbacks[0]()

        self.assertEqual(controller.state, PresentationState.CRASHED)
        self.assertEqual(controller_callbacks[-2:], ["tray", "child-exit:23"])

    def test_pipe_eof_is_reported_distinctly_from_child_exit(self) -> None:
        listener = Mock()
        connection = Mock()
        listener.accept.return_value = connection
        connection.recv_bytes.side_effect = EOFError
        process_release = threading.Event()
        process = Mock()
        process.wait.side_effect = lambda: process_release.wait(0.5) or 0
        controller_callbacks: list[object] = []
        controller = self.make_controller(Mock(return_value=listener), Mock(return_value=process), controller_callbacks)

        controller.launch()
        controller._reader_thread.join(0.5)
        ui_callbacks = [item for item in controller_callbacks if callable(item)]
        self.assertEqual(len(ui_callbacks), 1)
        ui_callbacks[0]()
        process_release.set()

        self.assertEqual(controller.state, PresentationState.CRASHED)
        self.assertEqual(controller_callbacks[-2:], ["tray", "pipe-eof"])

    def test_shutdown_terminates_then_kills_a_stuck_child_with_bounded_waits(self) -> None:
        listener = Mock()
        listener.accept.side_effect = OSError("closed")
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [None, subprocess.TimeoutExpired("child", 0.05)]
        controller = self.make_controller(Mock(return_value=listener), Mock(return_value=process), [])
        with patch.object(threading.Thread, "join", autospec=True) as join_mock:
            controller.launch()
            controller.shutdown()

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertTrue(all(item == call(0.05) or item.args[-1] == 0.05 for item in join_mock.call_args_list))
        self.assertEqual(controller.state, PresentationState.EXITED)


if __name__ == "__main__":
    unittest.main()
