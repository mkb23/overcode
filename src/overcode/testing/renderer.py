"""Render terminal output with ANSI codes to PNG images."""

from pathlib import Path
from typing import Optional
import pyte
from PIL import Image, ImageDraw, ImageFont


# Default terminal color palette (based on common terminal themes)
ANSI_COLORS = {
    "black": "#1e1e1e",
    "red": "#f44747",
    "green": "#6a9955",
    "yellow": "#dcdcaa",
    "blue": "#569cd6",
    "magenta": "#c586c0",
    "cyan": "#4ec9b0",
    "white": "#d4d4d4",
    # Bright variants
    "brightblack": "#808080",
    "brightred": "#f44747",
    "brightgreen": "#6a9955",
    "brightyellow": "#dcdcaa",
    "brightblue": "#569cd6",
    "brightmagenta": "#c586c0",
    "brightcyan": "#4ec9b0",
    "brightwhite": "#ffffff",
}

# Background color
BG_COLOR = "#1e1e1e"
DEFAULT_FG = "#d4d4d4"

# Font configuration
DEFAULT_FONT_SIZE = 14
FONT_PATHS = [
    # macOS
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/SFMono-Regular.otf",
    "/Library/Fonts/JetBrainsMono-Regular.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/JetBrainsMono-Regular.ttf",
]


def _find_monospace_font(size: int = DEFAULT_FONT_SIZE) -> ImageFont.FreeTypeFont:
    """Find a suitable monospace font."""
    for path in FONT_PATHS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Fallback to default
    return ImageFont.load_default()


def _color_256_to_hex(n: int) -> str:
    """Convert 256-color palette index to hex color."""
    # Colors 0-15: Standard colors (handled by name usually)
    standard_colors = [
        "#000000", "#800000", "#008000", "#808000", "#000080", "#800080", "#008080", "#c0c0c0",
        "#808080", "#ff0000", "#00ff00", "#ffff00", "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
    ]
    if n < 16:
        return standard_colors[n]

    # Colors 16-231: 6x6x6 color cube
    if n < 232:
        n -= 16
        r = (n // 36) * 51
        g = ((n // 6) % 6) * 51
        b = (n % 6) * 51
        return f"#{r:02x}{g:02x}{b:02x}"

    # Colors 232-255: Grayscale
    gray = (n - 232) * 10 + 8
    return f"#{gray:02x}{gray:02x}{gray:02x}"


def _pyte_color_to_hex(color, default: str, bright: bool = False) -> str:
    """Convert pyte color to hex color."""
    if color is None or color == "default":
        return default

    # Handle 256-color codes (pyte returns as int or string number)
    if isinstance(color, int):
        return _color_256_to_hex(color)

    if isinstance(color, str):
        # Handle 6-digit hex without # (pyte format for 24-bit color)
        # Must check this FIRST before trying int conversion
        if len(color) == 6 and all(c in "0123456789abcdefABCDEF" for c in color):
            return f"#{color}"

        # Try parsing as integer (256-color)
        if color.isdigit():
            return _color_256_to_hex(int(color))

        # Handle named colors
        if bright and color in ANSI_COLORS:
            bright_key = f"bright{color}"
            if bright_key in ANSI_COLORS:
                return ANSI_COLORS[bright_key]

        if color in ANSI_COLORS:
            return ANSI_COLORS[color]

        # Handle hex colors with #
        if color.startswith("#") and len(color) == 7:
            return color

    return default


def render_terminal_to_png(
    ansi_text: str,
    output_path: str,
    width: int = 120,
    height: int = 40,
    font_size: int = DEFAULT_FONT_SIZE,
    padding: int = 10,
) -> Path:
    """Render ANSI terminal text to a PNG image.

    Args:
        ansi_text: Terminal output with ANSI escape codes
        output_path: Path to save the PNG image
        width: Terminal width in characters
        height: Terminal height in characters
        font_size: Font size in pixels
        padding: Padding around the terminal content

    Returns:
        Path to the saved image
    """
    # Use a large internal buffer to prevent scrolling, then crop to actual content
    internal_height = max(height, 200)
    screen = pyte.Screen(width, internal_height)
    stream = pyte.Stream(screen)

    # Normalize line endings: \n -> \r\n for proper terminal emulation
    # (terminal needs carriage return + line feed to move to start of next line)
    normalized_text = ansi_text.replace('\r\n', '\n').replace('\n', '\r\n')

    # Feed the ANSI text through the terminal emulator
    stream.feed(normalized_text)

    # Find actual content bounds (non-empty rows)
    first_row = 0
    last_row = internal_height - 1
    for y in range(internal_height):
        row_has_content = any(
            screen.buffer[y][x].data.strip()
            for x in range(width)
            if hasattr(screen.buffer[y][x], 'data')
        )
        if row_has_content:
            if first_row == 0:
                first_row = y
            last_row = y

    # Use the requested height - the large internal buffer prevents scrolling,
    # and we render from first_row for `height` rows
    render_height = height

    # Load font and calculate dimensions
    font = _find_monospace_font(font_size)

    # Get character dimensions using a test character
    bbox = font.getbbox("M")
    char_width = bbox[2] - bbox[0]
    char_height = int(font_size * 1.4)  # Line height

    # Calculate image dimensions based on actual content
    img_width = width * char_width + 2 * padding
    img_height = render_height * char_height + 2 * padding

    # Create image with dark background
    img = Image.new("RGB", (img_width, img_height), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Render each character from content area
    for y in range(render_height):
        buffer_y = first_row + y
        if buffer_y >= internal_height:
            break
        for x in range(width):
            char = screen.buffer[buffer_y][x]

            # Get character and style
            char_data = char.data if hasattr(char, "data") else str(char)
            if not char_data or char_data == " ":
                continue

            # Get colors from character attributes
            fg_color = DEFAULT_FG
            bg_color = None

            if hasattr(char, "fg"):
                fg_color = _pyte_color_to_hex(
                    char.fg, DEFAULT_FG, bright=getattr(char, "bold", False)
                )
            if hasattr(char, "bg") and char.bg != "default":
                bg_color = _pyte_color_to_hex(char.bg, BG_COLOR)

            # Calculate position
            pos_x = padding + x * char_width
            pos_y = padding + y * char_height

            # Draw background if different from default
            if bg_color and bg_color != BG_COLOR:
                draw.rectangle(
                    [pos_x, pos_y, pos_x + char_width, pos_y + char_height],
                    fill=bg_color,
                )

            # Draw character
            draw.text((pos_x, pos_y), char_data, fill=fg_color, font=font)

    # Save image
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output)

    return output


def render_lines_to_png(
    lines: list[str],
    output_path: str,
    font_size: int = DEFAULT_FONT_SIZE,
    padding: int = 10,
    fg_color: str = DEFAULT_FG,
    bg_color: str = BG_COLOR,
) -> Path:
    """Render plain text lines to a PNG image (no ANSI parsing).

    This is a simpler alternative when you have plain text without ANSI codes.
    """
    font = _find_monospace_font(font_size)

    # Get character dimensions
    bbox = font.getbbox("M")
    char_width = bbox[2] - bbox[0]
    char_height = int(font_size * 1.4)

    # Calculate dimensions
    max_line_length = max(len(line) for line in lines) if lines else 1
    img_width = max_line_length * char_width + 2 * padding
    img_height = len(lines) * char_height + 2 * padding

    # Create image
    img = Image.new("RGB", (img_width, img_height), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Render lines
    for y, line in enumerate(lines):
        pos_y = padding + y * char_height
        draw.text((padding, pos_y), line, fill=fg_color, font=font)

    # Save
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output)

    return output
