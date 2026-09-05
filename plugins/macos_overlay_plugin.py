from __future__ import annotations

import os
import re
import textwrap
import tkinter as tk
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageTk

from plugin_api import (
    PLUGIN_API_VERSION,
    OverlayRenderer,
    PluginHostContext,
    VolumeStatus,
    plugin_ui_document,
    plugin_ui_result,
)
from plugins.windows11_overlay_plugin import (
    AUTO_HIDE_MS,
    ERROR_AUTO_HIDE_MS,
    OverlayPalette,
    VolumeOverlay,
)


MAC_HUD_SIZE = 200
MAC_HUD_RADIUS = 22
MAC_HUD_SEGMENTS = 16
MAC_TRANSPARENT = "#ff00ff"
MAC_ANTIALIAS_SCALE = 4
MAC_SPEAKER_SVG = (
    Path(__file__).resolve().parent / "macos_overlay" / "assets" / "macos-volume.svg"
)
MAC_ERROR_SVG = MAC_SPEAKER_SVG.with_name("macos-error.svg")
MAC_SUCCESS_SVG = MAC_SPEAKER_SVG.with_name("macos-success.svg")
MAC_SPEAKER_LEFT = 47
MAC_SPEAKER_TOP = 51
MAC_ROUTE_ICON_LEFT = 44
_SVG_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
MAC_ROUTE_ICON_PATHS = {
    route_type: MAC_SPEAKER_SVG.with_name(f"route-{route_type}.svg")
    for route_type in (
        "voice",
        "headset",
        "headphones",
        "earbuds",
        "soundbar",
        "tv",
        "avr",
        "amplifier",
        "microphone",
        "line-in",
        "line-out",
        "mixer",
        "monitor",
        "other",
    )
}
MAC_ROUTE_ICON_PATHS["speakers"] = MAC_SPEAKER_SVG


@dataclass(frozen=True)
class SpeakerSvgGeometry:
    width: float
    height: float
    body: tuple[tuple[float, float], ...]
    waves: tuple[tuple[tuple[float, float], ...], ...]
    stroke_width: float


@dataclass(frozen=True)
class IconSvgGeometry:
    width: float
    height: float
    circles: tuple[tuple[float, float, float, bool, float], ...]
    lines: tuple[tuple[tuple[tuple[float, float], ...], float], ...]


def calculate_filled_segments(volume: int, segment_count: int = MAC_HUD_SEGMENTS) -> int:
    """Map a 0-100 volume to the discrete blocks used by the macOS HUD."""
    if segment_count <= 0:
        return 0
    volume = max(0, min(int(volume), 100))
    return min(segment_count, (volume * segment_count + 50) // 100)


def speaker_wave_count(volume: int) -> int:
    volume = max(0, min(int(volume), 100))
    return 0 if volume == 0 else 1 if volume < 34 else 2 if volume < 67 else 3


def downsample_raster(raster: Image.Image) -> Image.Image:
    return raster.resize(
        (raster.width // MAC_ANTIALIAS_SCALE, raster.height // MAC_ANTIALIAS_SCALE),
        Image.Resampling.LANCZOS,
    )


@lru_cache(maxsize=1)
def load_speaker_svg(path: Path = MAC_SPEAKER_SVG) -> SpeakerSvgGeometry:
    """Load the controlled speaker SVG subset used by the Tk overlay renderer."""
    try:
        root = ET.parse(path).getroot()
        view_box = tuple(float(value) for value in root.attrib["viewBox"].split())
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        raise RuntimeError("The macOS volume icon SVG is unavailable or invalid.") from exc
    if len(view_box) != 4 or view_box[:2] != (0.0, 0.0):
        raise RuntimeError("The macOS volume icon SVG must use a zero-origin viewBox.")
    if view_box[2] <= 0 or view_box[3] <= 0:
        raise RuntimeError("The macOS volume icon SVG viewBox must have a positive size.")

    body: tuple[tuple[float, float], ...] = ()
    waves: list[tuple[int, tuple[tuple[float, float], ...], float]] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "polygon" and element.attrib.get("id") == "speaker":
            values = [float(value) for value in re.findall(_SVG_NUMBER, element.attrib.get("points", ""))]
            if len(values) < 6 or len(values) % 2:
                raise RuntimeError("The macOS volume icon SVG speaker polygon is invalid.")
            body = tuple(zip(values[0::2], values[1::2]))
        elif tag == "path" and "data-wave" in element.attrib:
            try:
                level = int(element.attrib["data-wave"])
                stroke_width = float(element.attrib["stroke-width"])
            except (KeyError, ValueError) as exc:
                raise RuntimeError("The macOS volume icon SVG wave metadata is invalid.") from exc
            waves.append((level, _sample_cubic_svg_path(element.attrib.get("d", "")), stroke_width))

    waves.sort(key=lambda item: item[0])
    if not body or [level for level, _points, _width in waves] != [1, 2, 3]:
        raise RuntimeError("The macOS volume icon SVG geometry is incomplete.")
    stroke_widths = {stroke_width for _level, _points, stroke_width in waves}
    if len(stroke_widths) != 1 or next(iter(stroke_widths)) <= 0:
        raise RuntimeError("The macOS volume icon SVG wave widths must match.")
    all_points = body + tuple(point for _level, points, _width in waves for point in points)
    if any(
        x < 0 or y < 0 or x > view_box[2] or y > view_box[3]
        for x, y in all_points
    ):
        raise RuntimeError("The macOS volume icon SVG geometry exceeds its viewBox.")
    return SpeakerSvgGeometry(
        width=view_box[2],
        height=view_box[3],
        body=body,
        waves=tuple(points for _level, points, _width in waves),
        stroke_width=stroke_widths.pop(),
    )


def _sample_cubic_svg_path(path_data: str) -> tuple[tuple[float, float], ...]:
    commands = re.sub(_SVG_NUMBER, "", path_data).replace(",", "").split()
    values = [float(value) for value in re.findall(_SVG_NUMBER, path_data)]
    if commands != ["M", "C"] or len(values) != 8:
        raise RuntimeError("The macOS volume icon SVG uses unsupported path geometry.")
    start = values[0], values[1]
    control_one = values[2], values[3]
    control_two = values[4], values[5]
    end = values[6], values[7]
    points = []
    for index in range(25):
        t = index / 24
        inverse = 1 - t
        points.append(
            (
                (inverse**3 * start[0])
                + (3 * inverse**2 * t * control_one[0])
                + (3 * inverse * t**2 * control_two[0])
                + (t**3 * end[0]),
                (inverse**3 * start[1])
                + (3 * inverse**2 * t * control_one[1])
                + (3 * inverse * t**2 * control_two[1])
                + (t**3 * end[1]),
            )
        )
    return tuple(points)


@lru_cache(maxsize=32)
def load_icon_svg(path: Path) -> IconSvgGeometry:
    """Load the controlled circle/line subset used by macOS overlay icons."""
    try:
        root = ET.parse(path).getroot()
        view_box = tuple(float(value) for value in root.attrib["viewBox"].split())
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        raise RuntimeError("A macOS overlay status SVG is unavailable or invalid.") from exc
    if (
        len(view_box) != 4
        or view_box[:2] != (0.0, 0.0)
        or view_box[2] <= 0
        or view_box[3] <= 0
    ):
        raise RuntimeError("A macOS overlay status SVG has an invalid viewBox.")

    circles: list[tuple[float, float, float, bool, float]] = []
    lines: list[tuple[tuple[tuple[float, float], ...], float]] = []
    try:
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "circle":
                stroke_width = float(element.attrib.get("stroke-width", "0"))
                circles.append(
                    (
                        float(element.attrib["cx"]),
                        float(element.attrib["cy"]),
                        float(element.attrib["r"]),
                        element.attrib.get("fill") != "none",
                        stroke_width,
                    )
                )
            elif tag == "line":
                lines.append(
                    (
                        (
                            (float(element.attrib["x1"]), float(element.attrib["y1"])),
                            (float(element.attrib["x2"]), float(element.attrib["y2"])),
                        ),
                        float(element.attrib["stroke-width"]),
                    )
                )
            elif tag == "polyline":
                values = [
                    float(value)
                    for value in re.findall(_SVG_NUMBER, element.attrib["points"])
                ]
                if len(values) < 4 or len(values) % 2:
                    raise ValueError
                lines.append(
                    (
                        tuple(zip(values[0::2], values[1::2])),
                        float(element.attrib["stroke-width"]),
                    )
                )
    except (KeyError, ValueError) as exc:
        raise RuntimeError("A macOS overlay status SVG has invalid geometry.") from exc
    if not circles and not lines:
        raise RuntimeError("A macOS overlay SVG has no supported geometry.")
    if any(
        radius <= 0 or stroke_width < 0
        for _x, _y, radius, _fill, stroke_width in circles
    ):
        raise RuntimeError("A macOS overlay status SVG has incomplete circle geometry.")
    if any(width <= 0 for _points, width in lines):
        raise RuntimeError("A macOS overlay status SVG has invalid line geometry.")
    if any(
        center_x - radius < 0
        or center_y - radius < 0
        or center_x + radius > view_box[2]
        or center_y + radius > view_box[3]
        for center_x, center_y, radius, _filled, _stroke_width in circles
    ) or any(
        x < 0 or y < 0 or x > view_box[2] or y > view_box[3]
        for points, _stroke_width in lines
        for x, y in points
    ):
        raise RuntimeError("A macOS overlay status SVG geometry exceeds its viewBox.")
    return IconSvgGeometry(view_box[2], view_box[3], tuple(circles), tuple(lines))


def rounded_rectangle_points(
    left: int,
    top: int,
    right: int,
    bottom: int,
    radius: int,
) -> tuple[int, ...]:
    """Return repeated corner points suitable for a smooth Tk canvas polygon."""
    radius = max(0, min(radius, (right - left) // 2, (bottom - top) // 2))
    return (
        left + radius, top,
        right - radius, top,
        right, top,
        right, top + radius,
        right, bottom - radius,
        right, bottom,
        right - radius, bottom,
        left + radius, bottom,
        left, bottom,
        left, bottom - radius,
        left, top + radius,
        left, top,
    )


class MacOSVolumeOverlay(VolumeOverlay):
    """Classic macOS volume HUD rendered through the shared focus-safe window path."""

    @staticmethod
    def _get_palette(dark_mode: bool, high_contrast: bool) -> OverlayPalette:
        if high_contrast:
            return OverlayPalette(
                "SystemWindow",
                "SystemWindowText",
                "SystemWindowText",
                "SystemWindowText",
                "SystemHighlight",
                "SystemWindowText",
                "SystemWindow",
                1.0,
            )
        if dark_mode:
            return OverlayPalette(
                "#242426",
                "#242426",
                "#FFFFFF",
                "#D1D1D6",
                "#FFFFFF",
                "#FF6961",
                "#68686C",
                0.90,
            )
        return OverlayPalette(
            "#3A3A3C",
            "#3A3A3C",
            "#FFFFFF",
            "#E5E5EA",
            "#FFFFFF",
            "#FF6961",
            "#7C7C80",
            0.92,
        )

    def __init__(self, root: tk.Tk, dark_mode: bool = True, high_contrast: bool = False) -> None:
        self.root = root
        self._hide_after_id: str | None = None
        self._palette = self._get_palette(dark_mode, high_contrast)
        self._high_contrast = high_contrast
        self._presentation: tuple[object, ...] = ("volume", 0)
        self._hud_photo: ImageTk.PhotoImage | None = None

        self.window = tk.Toplevel(root, takefocus=False)
        self.window.withdraw()
        self.window.overrideredirect(True)
        try:
            self.window.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.window,
            width=MAC_HUD_SIZE,
            height=MAC_HUD_SIZE,
            bd=0,
            highlightthickness=0,
        )
        self.canvas.pack()
        self.apply_theme(dark_mode, high_contrast)
        self.window.update_idletasks()
        self._configure_no_activate()

    def apply_theme(self, dark_mode: bool, high_contrast: bool = False) -> None:
        self._palette = self._get_palette(dark_mode, high_contrast)
        self._high_contrast = high_contrast
        canvas_background = self._palette.background if high_contrast else MAC_TRANSPARENT
        self.window.configure(bg=canvas_background)
        self.canvas.configure(bg=canvas_background)
        try:
            self.window.attributes("-alpha", self._palette.alpha)
            self.window.attributes("-transparentcolor", "" if high_contrast else MAC_TRANSPARENT)
        except tk.TclError:
            pass
        self._render_presentation()

    def show(self, volume: int, preferred_display_device_name: str | None = None) -> None:
        volume = max(0, min(int(volume), 100))
        self._presentation = ("volume", volume)
        self._draw_volume_hud(volume, "speakers")
        self._show_window(AUTO_HIDE_MS, preferred_display_device_name)

    def show_error(self, message: str, preferred_display_device_name: str | None = None) -> None:
        message = message.strip() or "Selected monitor is unavailable."
        self._presentation = ("error", message)
        self._draw_message_hud(message, error=True)
        self._show_window(ERROR_AUTO_HIDE_MS, preferred_display_device_name)

    def show_text(self, text: str, preferred_display_device_name: str | None = None) -> None:
        text = text.strip() or "Done"
        self._presentation = ("text", text)
        self._draw_message_hud(text, error=False)
        self._show_window(AUTO_HIDE_MS, preferred_display_device_name)

    def show_statuses(
        self,
        statuses: tuple[VolumeStatus, ...],
        current_provider_id: str | None,
        preferred_display_device_name: str | None = None,
    ) -> None:
        statuses = self.select_statuses(statuses, current_provider_id)
        if not statuses:
            self.show_error("No routed volume providers are available.", preferred_display_device_name)
            return
        status = statuses[0]
        self._presentation = ("statuses", statuses, current_provider_id)
        if status.confirmed_volume is None:
            detail = status.reason or "Unavailable"
            self._draw_message_hud(f"{status.display_name}\n{detail}", error=True)
        else:
            self._draw_volume_hud(status.confirmed_volume, status.route_type)
        self._show_window(AUTO_HIDE_MS, preferred_display_device_name)

    def select_statuses(
        self,
        statuses: tuple[VolumeStatus, ...],
        current_provider_id: str | None,
    ) -> tuple[VolumeStatus, ...]:
        if current_provider_id is not None:
            current = tuple(
                status for status in statuses if status.provider_id == current_provider_id
            )
            if current:
                return current[:1]
        return statuses[:1]

    def _render_presentation(self) -> None:
        kind = self._presentation[0]
        if kind == "volume":
            self._draw_volume_hud(int(self._presentation[1]), "speakers")
        elif kind == "error":
            self._draw_message_hud(str(self._presentation[1]), error=True)
        elif kind == "text":
            self._draw_message_hud(str(self._presentation[1]), error=False)
        elif kind == "statuses":
            statuses = self._presentation[1]
            status = statuses[0]
            if status.confirmed_volume is None:
                self._draw_message_hud(
                    f"{status.display_name}\n{status.reason or 'Unavailable'}",
                    error=True,
                )
            else:
                self._draw_volume_hud(status.confirmed_volume, status.route_type)

    def _prepare_canvas(self, width: int, height: int) -> None:
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        inset = 2
        radius = 0 if self._high_contrast else MAC_HUD_RADIUS
        self.canvas.create_polygon(
            rounded_rectangle_points(inset, inset, width - inset, height - inset, radius),
            smooth=not self._high_contrast,
            splinesteps=36,
            fill=self._palette.background,
            outline=self._palette.border if self._high_contrast else self._palette.background,
            width=2 if self._high_contrast else 1,
        )

    def _draw_volume_hud(self, volume: int, route_type: str = "speakers") -> None:
        self._prepare_canvas(MAC_HUD_SIZE, MAC_HUD_SIZE)
        raster = self._new_raster(MAC_HUD_SIZE, MAC_HUD_SIZE)
        draw = ImageDraw.Draw(raster)
        icon_path = MAC_ROUTE_ICON_PATHS.get(route_type, MAC_ROUTE_ICON_PATHS["other"])
        if icon_path == MAC_SPEAKER_SVG:
            self._draw_speaker(draw, volume)
        else:
            self._draw_svg_icon(
                draw,
                icon_path,
                MAC_ROUTE_ICON_LEFT,
                MAC_SPEAKER_TOP,
                self._image_color(self._palette.text),
            )
        self._draw_segments(draw, 21, 160, 158, volume)
        self._show_raster(raster)

    def _draw_speaker(
        self,
        draw: ImageDraw.ImageDraw,
        volume: int,
    ) -> None:
        factor = MAC_ANTIALIAS_SCALE
        color = self._image_color(self._palette.text)
        geometry = load_speaker_svg()
        body = tuple(
            (
                (MAC_SPEAKER_LEFT + x) * factor,
                (MAC_SPEAKER_TOP + y) * factor,
            )
            for x, y in geometry.body
        )
        draw.polygon(body, fill=color)
        for wave in geometry.waves[:speaker_wave_count(volume)]:
            points = tuple(
                (
                    (MAC_SPEAKER_LEFT + x) * factor,
                    (MAC_SPEAKER_TOP + y) * factor,
                )
                for x, y in wave
            )
            draw.line(
                points,
                fill=color,
                width=round(geometry.stroke_width * factor),
                joint="curve",
            )

    def _draw_segments(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        top: int,
        width: int,
        volume: int,
    ) -> None:
        factor = MAC_ANTIALIAS_SCALE
        gap = 3
        segment_width = (width - (gap * (MAC_HUD_SEGMENTS - 1))) / MAC_HUD_SEGMENTS
        filled = calculate_filled_segments(volume)
        for index in range(MAC_HUD_SEGMENTS):
            x = left + (index * (segment_width + gap))
            draw.rectangle(
                (
                    round(x * factor),
                    top * factor,
                    round((x + segment_width) * factor),
                    (top + 10) * factor,
                ),
                fill=self._image_color(
                    self._palette.accent if index < filled else self._palette.track
                ),
            )

    def _draw_message_hud(self, message: str, error: bool) -> None:
        self._prepare_canvas(MAC_HUD_SIZE, MAC_HUD_SIZE)
        raster = self._new_raster(MAC_HUD_SIZE, MAC_HUD_SIZE)
        draw = ImageDraw.Draw(raster)
        factor = MAC_ANTIALIAS_SCALE
        color = self._image_color(self._palette.error if error else self._palette.text)
        self._draw_svg_icon(
            draw,
            MAC_ERROR_SVG if error else MAC_SUCCESS_SVG,
            74,
            35,
            color,
        )
        wrapped = "\n".join(
            line
            for paragraph in message.splitlines() or [message]
            for line in (textwrap.wrap(paragraph, width=24) or [""])
        )
        draw.multiline_text(
            (100 * factor, 128 * factor),
            wrapped,
            fill=self._image_color(self._palette.text),
            font=self._image_font(12),
            anchor="mm",
            align="center",
            spacing=4 * factor,
        )
        self._show_raster(raster)

    @staticmethod
    def _draw_svg_icon(
        draw: ImageDraw.ImageDraw,
        path: Path,
        left: int,
        top: int,
        color: tuple[int, int, int, int],
    ) -> None:
        factor = MAC_ANTIALIAS_SCALE
        geometry = load_icon_svg(path)
        for center_x, center_y, radius, filled, stroke_width in geometry.circles:
            bounds = (
                (left + center_x - radius) * factor,
                (top + center_y - radius) * factor,
                (left + center_x + radius) * factor,
                (top + center_y + radius) * factor,
            )
            if filled:
                draw.ellipse(bounds, fill=color)
            else:
                draw.ellipse(bounds, outline=color, width=round(stroke_width * factor))
        for points, stroke_width in geometry.lines:
            scaled = tuple(
                ((left + x) * factor, (top + y) * factor)
                for x, y in points
            )
            width = round(stroke_width * factor)
            draw.line(scaled, fill=color, width=width, joint="curve")
            radius = width / 2
            for x, y in (scaled[0], scaled[-1]):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    @staticmethod
    def _new_raster(width: int, height: int) -> Image.Image:
        factor = MAC_ANTIALIAS_SCALE
        return Image.new("RGBA", (width * factor, height * factor), (0, 0, 0, 0))

    def _show_raster(self, raster: Image.Image) -> None:
        rendered = downsample_raster(raster)
        self._hud_photo = ImageTk.PhotoImage(rendered, master=self.window)
        self.canvas.create_image(0, 0, image=self._hud_photo, anchor="nw")

    def _image_color(self, color: str) -> tuple[int, int, int, int]:
        try:
            red, green, blue = ImageColor.getrgb(color)
        except ValueError:
            red16, green16, blue16 = self.window.winfo_rgb(color)
            red, green, blue = red16 // 257, green16 // 257, blue16 // 257
        return red, green, blue, 255

    @staticmethod
    def _image_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_name = "seguisb.ttf" if bold else "segoeui.ttf"
        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / font_name
        fallback_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        for candidate in (str(font_path), fallback_name):
            try:
                return ImageFont.truetype(candidate, size * MAC_ANTIALIAS_SCALE)
            except OSError:
                continue
        return ImageFont.load_default(size=size * MAC_ANTIALIAS_SCALE)


class MacOSOverlayPlugin:
    plugin_id = "macos-overlay"
    name = "macOS volume HUD"
    description = "Classic macOS translucent volume HUD with a speaker and segmented meter."

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def get_plugin_ui(self) -> dict[str, object]:
        return plugin_ui_document(
            "macOS volume HUD settings",
            [],
            [{"id": "test", "label": "Test", "kind": "action", "async": False}],
            "Preview the classic macOS volume presentation.",
        )

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "test":
            raise ValueError(f"Unknown macOS overlay UI action {action_id!r}.")
        if self._host is None:
            raise RuntimeError("Overlay plugin has not been initialized.")
        self._host.show_overlay_preview()
        return plugin_ui_result("complete", message="Overlay test displayed.")

    def create_overlay_renderer(self, dark_mode: bool, high_contrast: bool) -> OverlayRenderer:
        if self._host is None:
            raise RuntimeError("Overlay plugin has not been initialized.")
        return MacOSVolumeOverlay(self._host.ui_parent, dark_mode, high_contrast)

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> MacOSOverlayPlugin:
    return MacOSOverlayPlugin()
