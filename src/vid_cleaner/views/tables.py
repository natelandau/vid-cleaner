"""Tables for the VidCleaner application."""

from box import Box
from rich.table import Table


def stream_table(ffprobe_box: Box) -> Table:
    """Create a formatted table displaying video stream information.

    Display details about video, audio and subtitle streams in a terminal-friendly format. The table includes stream index, codec type, language, audio channels, dimensions and titles.

    Args:
        ffprobe_box (Box): Box object containing ffprobe output with stream information

    Returns:
        Table: Rich table containing formatted stream information
    """
    table = Table(title=ffprobe_box.name)
    table.add_column("#")
    table.add_column("Type")
    table.add_column("Codec Name")
    table.add_column("Language")
    table.add_column("Channels")
    table.add_column("Channel Layout")
    table.add_column("Width")
    table.add_column("Height")
    table.add_column("Title")

    for stream in ffprobe_box.streams:
        table.add_row(
            str(stream.index),
            stream.codec_type.value,
            stream.codec_name,
            stream.language,
            str(stream.channels.value) if stream.channels else "",
            stream.channel_layout or "",
            str(stream.width) if stream.width else "",
            str(stream.height) if stream.height else "",
            stream.title or "",
        )

    return table
