import argparse

from monitorcontrol import InputSource, get_input_name, get_monitors
from monitorcontrol.vcp import VCPError


def describe_input(value: int) -> str:
    try:
        name = get_input_name(value)
    except ValueError:
        name = "Unknown"
    return f"{name} (0x{value:02X})"


def parse_input(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError:
        try:
            return InputSource[value.upper()].value
        except KeyError as exc:
            names = ", ".join(source.name for source in InputSource)
            raise argparse.ArgumentTypeError(
                f"unknown input {value!r}; use a numeric code or one of: {names}"
            ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List monitor input sources or change one monitor's active input."
    )
    parser.add_argument("--monitor", type=int, help="one-based monitor number")
    parser.add_argument(
        "--input",
        type=parse_input,
        help="input name or numeric VCP code, for example HDMI1 or 0x11",
    )
    args = parser.parse_args()
    if (args.monitor is None) != (args.input is None):
        parser.error("--monitor and --input must be used together")
    return args


def change_input(monitors: list, monitor_number: int, target: int) -> int:
    if monitor_number < 1 or monitor_number > len(monitors):
        print(f"Monitor number must be between 1 and {len(monitors)}.")
        return 2

    monitor = monitors[monitor_number - 1]
    name = monitor.vcp.description or "Unknown monitor"
    try:
        with monitor:
            capabilities = monitor.get_vcp_capabilities()
            name = capabilities.get("model") or name
            inputs = capabilities.get("inputs") or []
            if not inputs:
                print(f"Monitor {monitor_number} ({name}) does not advertise any inputs.")
                return 1
            if target not in inputs:
                available = ", ".join(describe_input(value) for value in inputs)
                print(
                    f"Monitor {monitor_number} ({name}) does not advertise "
                    f"{describe_input(target)}. Available: {available}"
                )
                return 2

            current = monitor.get_input_source()
            if current == target:
                print(f"Monitor {monitor_number} ({name}) is already on {describe_input(target)}.")
                return 0

            monitor.set_input_source(target)
            try:
                confirmed = monitor.get_input_source()
            except (VCPError, OSError, ValueError) as exc:
                print(
                    f"Monitor {monitor_number} ({name}): requested {describe_input(target)}, "
                    f"but could not verify the new input: {exc}"
                )
                return 1
    except (VCPError, OSError, ValueError) as exc:
        print(f"Monitor {monitor_number} ({name}): input change failed: {exc}")
        return 1

    if confirmed != target:
        print(
            f"Monitor {monitor_number} ({name}): requested {describe_input(target)}, "
            f"but the monitor reports {describe_input(confirmed)}."
        )
        return 1

    print(
        f"Monitor {monitor_number} ({name}): changed "
        f"{describe_input(current)} -> {describe_input(confirmed)}"
    )
    return 0


def main() -> int:
    args = parse_args()
    try:
        monitors = get_monitors()
    except (NotImplementedError, VCPError, OSError) as exc:
        print(f"Could not enumerate monitors: {exc}")
        return 1

    if not monitors:
        print("No DDC/CI monitors found.")
        return 1

    if args.monitor is not None:
        return change_input(monitors, args.monitor, args.input)

    failed = False
    for index, monitor in enumerate(monitors, start=1):
        name = monitor.vcp.description or "Unknown monitor"
        inputs = None
        current = None
        capability_error = None
        current_error = None
        try:
            with monitor:
                try:
                    capabilities = monitor.get_vcp_capabilities()
                    name = capabilities.get("model") or name
                    inputs = capabilities.get("inputs") or []
                except (VCPError, OSError, ValueError) as exc:
                    capability_error = exc
                try:
                    current = monitor.get_input_source()
                except (VCPError, OSError, ValueError) as exc:
                    current_error = exc
        except (VCPError, OSError) as exc:
            print(f"Monitor {index}: DDC/CI unavailable, skipped ({exc})")
            continue

        print(f"Monitor {index}: {name}")
        if capability_error is not None and current_error is not None:
            print("  DDC/CI input information unavailable, skipped")
            continue
        if current_error is not None:
            print(f"  Current: read failed: {current_error}")
            failed = True
        else:
            print(f"  Current: {describe_input(current)}")
        if capability_error is not None:
            print(f"  Possible inputs: read failed: {capability_error}")
            failed = True
        elif not inputs:
            print("  Possible inputs: not advertised by the monitor")
        else:
            print("  Possible inputs:")
            for value in inputs:
                marker = "*" if value == current else " "
                print(f"   {marker} {describe_input(value)}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
