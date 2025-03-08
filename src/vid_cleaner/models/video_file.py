"""VideoFile model."""

import atexit
import re
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

import cappa
from ffmpeg_progress_yield import FfmpegProgress
from iso639 import Lang
from rich.markdown import Markdown
from rich.progress import Progress
from rich.table import Table

from vid_cleaner.constants import (
    EXCLUDED_VIDEO_CODECS,
    FFMPEG_APPEND,
    FFMPEG_PREPEND,
    H265_CODECS,
    SYMBOL_CHECK,
    AudioLayout,
    CodecTypes,
)
from vid_cleaner.utils import (
    channels_to_layout,
    console,
    pp,
    query_radarr,
    query_sonarr,
    query_tmdb,
    run_ffprobe,
    settings,
)

from vid_cleaner.controllers import TempFile  # isort: skip


def cleanup_on_exit(video_file: "VideoFile") -> None:  # pragma: no cover
    """Cleanup temporary files on exit.

    Args:
        video_file (VideoFile): The VideoFile object to perform cleanup on.
    """
    video_file.cleanup()


@dataclass
class VideoStream:
    """VideoStream model."""

    index: int
    codec_name: str
    codec_long_name: str
    codec_type: CodecTypes
    duration: str | None
    width: int | None
    height: int | None
    bps: int | None
    sample_rate: int | None
    language: str | None
    channels: AudioLayout | None
    channel_layout: str | None
    layout: str | None
    title: str | None


@dataclass
class VideoProbe:
    """VideoProbe model."""

    name: str
    streams: list[VideoStream]
    format_name: str | None
    format_long_name: str | None
    duration: str | None
    start_time: float | None
    size: int | None
    bit_rate: int | None
    json_data: dict

    @classmethod
    def parse_probe_response(cls, json_obj: dict, stem: str) -> "VideoProbe":
        """Parse ffprobe JSON output into a VideoProbe object.

        Extract relevant stream and format information from ffprobe's JSON output and create a structured VideoProbe instance.

        Args:
            json_obj (dict): Raw ffprobe JSON output
            stem (str): Base filename without extension

        Returns:
            VideoProbe: Structured representation of the video file's properties
        """
        # Find name
        if "title" in json_obj["format"]["tags"]:
            name = json_obj["format"]["tags"]["title"]
        elif "filename" in json_obj["format"]:
            name = json_obj["format"]["filename"]
        else:
            name = stem

        # Find streams
        streams = [
            VideoStream(
                index=stream["index"],
                codec_name=stream.get("codec_name", ""),
                codec_long_name=stream.get("codec_long_name", ""),
                codec_type=CodecTypes(stream["codec_type"].lower()),
                duration=stream.get("duration", None),
                width=stream.get("width", None),
                height=stream.get("height", None),
                bps=stream.get("tags", {}).get("BPS", None),
                sample_rate=stream.get("sample_rate", None),
                language=stream.get("language", None)
                or stream.get("tags", {}).get("language", None),
                channels=channels_to_layout(stream.get("channels", None)),
                channel_layout=stream.get("channel_layout", None),
                layout=stream.get("layout", None),
                title=stream.get("tags", {}).get("title", None),
            )
            for stream in json_obj["streams"]
        ]

        return cls(
            name=name,
            format_name=json_obj["format"].get("format_name", None),
            format_long_name=json_obj["format"].get("format_long_name", None),
            duration=json_obj["format"].get("duration", None),
            start_time=json_obj["format"].get("start_time", None),
            size=json_obj["format"].get("size", None),
            bit_rate=json_obj["format"].get("bit_rate", None),
            streams=streams,
            json_data=json_obj,
        )

    def as_table(self) -> Table:
        """Format video stream information as a Rich table.

        Create a formatted table showing details about video, audio and subtitle streams for display in the terminal.

        Returns:
            Table: Rich table containing stream information
        """
        table = Table(title=self.name)
        table.add_column("#")
        table.add_column("Type")
        table.add_column("Codec Name")
        table.add_column("Language")
        table.add_column("Channels")
        table.add_column("Channel Layout")
        table.add_column("Width")
        table.add_column("Height")
        table.add_column("Title")

        for stream in self.streams:
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


class VideoFile:
    """VideoFile model."""

    def __init__(self, path: Path) -> None:
        """Initialize VideoFile."""
        self.path = path.expanduser().resolve()
        self.name = path.name
        self.stem = path.stem
        self.parent = path.parent
        self.suffix = path.suffix
        self.suffixes = self.path.suffixes
        self.temp_file = TempFile(self.path)

        self.container = self.suffix
        self.language: Lang = None
        self.ran_language_check = False

        # Register cleanup on exit
        atexit.register(cleanup_on_exit, self)

    @staticmethod
    def _downmix_to_stereo(streams: list[VideoStream]) -> list[str]:
        """Generate a partial ffmpeg command to downmix audio streams to stereo if needed.

        Analyze the provided audio streams and construct a command to downmix 5.1 or 7.1 audio streams to stereo. Handle cases where stereo is already present or needs to be created from surround sound streams.

        Args:
            streams (list[VideoStream]): List of audio stream dictionaries.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for audio downmixing.
        """
        downmix_command: list[str] = []
        new_index = 0
        has_stereo = False
        surround5 = []  # index of 5.1 streams
        surround7 = []  # index of 7.1 streams

        for stream in streams:
            match stream.channels:
                case AudioLayout.STEREO:
                    has_stereo = True
                case AudioLayout.SURROUND5:
                    surround5.append(stream)
                case AudioLayout.SURROUND7:
                    surround7.append(stream)
                case AudioLayout.MONO:
                    pass
                case _:
                    assert_never(stream.channels)

        if not has_stereo and surround5:
            for surround5_stream in surround5:
                downmix_command.extend(
                    [
                        "-map",
                        f"0:{surround5_stream.index}",
                        f"-c:a:{new_index}",
                        "aac",
                        f"-ac:a:{new_index}",
                        "2",
                        f"-b:a:{new_index}",
                        "256k",
                        f"-filter:a:{new_index}",
                        "pan=stereo|FL=FC+0.30*FL+0.30*FLC+0.30*BL+0.30*SL+0.60*LFE|FR=FC+0.30*FR+0.30*FRC+0.30*BR+0.30*SR+0.60*LFE,loudnorm",
                        f"-ar:a:{new_index}",
                        "48000",
                        f"-metadata:s:a:{new_index}",
                        "title=2.0",
                    ],
                )
                new_index += 1
                has_stereo = True

        if not has_stereo and surround7:
            pp.debug(
                "PROCESS AUDIO: Audio track is 5 channel, no 2 channel exists. Creating 2 channel from 5 channel",
            )

            for surround7_stream in surround7:
                downmix_command.extend(
                    [
                        "-map",
                        f"0:{surround7_stream.index}",
                        f"-c:a:{new_index}",
                        "aac",
                        f"-ac:a:{new_index}",
                        "2",
                        f"-b:a:{new_index}",
                        "256k",
                        f"-metadata:s:a:{new_index}",
                        "title=2.0",
                    ],
                )
                new_index += 1

        pp.trace(f"PROCESS AUDIO: Downmix command: {downmix_command}")
        return downmix_command

    def _find_original_language(self) -> Lang:  # pragma: no cover
        """Find the original language of the video content.

        Query external APIs (IMDb, TMDB, Radarr, Sonarr) to determine the original language. Cache results to avoid repeated API calls.

        Returns:
            Lang: The determined original language code
        """
        # Only run the API calls once
        if self.ran_language_check:
            return self.language

        original_language = None

        # Try to find the IMDb ID
        match = re.search(r"(tt\d+)", self.stem)
        imdb_id = match.group(0) if match else self._query_arr_apps_for_imdb_id()

        # Query TMDB for the original language
        response = query_tmdb(imdb_id) if imdb_id else None

        if response and (tmdb_response := response.get("movie_results", [{}])[0]):
            original_language = tmdb_response.get("original_language")
            pp.trace(f"TMDB: Original language: {original_language}")

        if not original_language:
            pp.debug(f"Could not find original language for: {self.name}")
            return None

        # If the original language is pulled as Chinese (cn). iso639 expects 'zh' for Chinese.
        if original_language == "cn":
            original_language = "zh"

        try:
            language = Lang(original_language)
        except Exception:  # noqa: BLE001
            pp.debug(f"iso639: Could not find language for: {self.name}")
            return None

        # Set language attribute
        self.language = language
        self.ran_language_check = True
        return language

    def _get_probe(self) -> VideoProbe:  # pragma: no cover
        """Retrieve the ffprobe probe information for the video.

        Fetch detailed information about the video file using ffprobe. Optionally filter the information by a specific key.

        Returns:
            VideoProbe: The ffprobe probe information.
        """
        input_path = self.temp_file.latest_temp_path()

        return VideoProbe.parse_probe_response(run_ffprobe(input_path), self.stem)

    @staticmethod
    def _process_video(streams: list[VideoStream]) -> list[str]:
        """Create a command list for processing video streams.

        Iterate through the provided video streams and construct a list of ffmpeg commands to process them, excluding any streams with codecs in the exclusion list.

        Args:
            streams (list[dict]): A list of video stream dictionaries.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for video processing.
        """
        command: list[str] = []
        for stream in streams:
            if stream.codec_name.lower() in EXCLUDED_VIDEO_CODECS:
                continue

            command.extend(["-map", f"0:{stream.index}"])

        pp.trace(f"PROCESS VIDEO: {command}")
        return command

    def _process_subtitles(
        self,
        streams: list[VideoStream],
    ) -> list[str]:
        """Construct a command list for processing subtitle streams.

        Analyze and filter subtitle streams based on language preferences, commentary options, and other criteria. Build an ffmpeg command list accordingly.

        Args:
            streams (list[VideoStream]): A list of subtitle stream objects.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for subtitle processing.
        """
        command: list[str] = []

        langs = [Lang(lang) for lang in settings.langs_to_keep]

        # Find original language
        if not settings.subs_drop_local:
            original_language = self._find_original_language()

        # Return no streams if no languages are specified
        if (
            not settings.keep_all_subtitles
            and not settings.keep_local_subtitles
            and settings.subs_drop_local
        ):
            return command

        for stream in streams:
            if (
                not settings.keep_commentary
                and stream.title is not None
                and re.search(r"commentary|sdh|description", stream.title, re.IGNORECASE)
            ):
                pp.trace(rf"PROCESS SUBTITLES: Remove stream #{stream.index} \[commentary]")
                continue

            if settings.keep_all_subtitles:
                command.extend(["-map", f"0:{stream.index}"])
                continue

            if stream.language:
                if settings.keep_local_subtitles and (
                    stream.language.lower() == "und" or Lang(stream.language) in langs
                ):
                    pp.trace(f"PROCESS SUBTITLES: Keep stream #{stream.index} (local language)")
                    command.extend(["-map", f"0:{stream.index}"])
                    continue

                if (
                    not settings.subs_drop_local
                    and langs
                    and original_language not in langs
                    and (stream.language.lower == "und" or Lang(stream.language) in langs)
                ):
                    pp.trace(f"PROCESS SUBTITLES: Keep stream #{stream.index} (original language)")
                    command.extend(["-map", f"0:{stream.index}"])
                    continue

            pp.trace(f"PROCESS SUBTITLES: Remove stream #{stream.index}")

        pp.trace(f"PROCESS SUBTITLES: {command}")
        return command

    def _process_audio(
        self,
        streams: list[VideoStream],
    ) -> tuple[list[str], list[str]]:
        """Construct commands for processing audio streams.

        Analyze and process audio streams based on language, commentary, and downmixing criteria. Generate ffmpeg commands for keeping or altering audio streams as required.

        Args:
            streams (list[VideoStream]): A list of audio stream objects.

        Returns:
            tuple[list[str], list[str]]: A tuple containing two lists of strings forming part of an ffmpeg command for audio processing.
        """
        command: list[str] = []

        # Turn language codes into iso639 objects
        langs = [Lang(lang) for lang in settings.langs_to_keep]

        # Add original language to list of languages to keep
        if not settings.drop_original_audio:
            original_language = self._find_original_language()
            if original_language and original_language not in langs:
                langs.append(original_language)

        streams_to_keep = []
        for stream in streams:
            # Keep unknown language streams
            if not stream.language:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)
                continue

            # Remove commentary streams
            if (
                not settings.keep_commentary
                and stream.title
                and re.search(r"commentary|sdh|description", stream.title, re.IGNORECASE)
            ):
                pp.trace(rf"PROCESS AUDIO: Remove stream #{stream.index} \[commentary]")
                continue

            # Keep streams with specified languages
            if stream.language == "und" or Lang(stream.language) in langs:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)
                continue

            pp.trace(f"PROCESS AUDIO: Remove stream #{stream.index}")

        # Failsafe to cancel processing if all streams would be removed following this plugin. We don't want no audio.
        if not command:
            for stream in streams:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)

        # Downmix to stereo if needed
        downmix_command = (
            self._downmix_to_stereo(streams_to_keep) if settings.downmix_stereo else []
        )

        pp.trace(f"PROCESS AUDIO: {command}")
        return command, downmix_command

    def _query_arr_apps_for_imdb_id(self) -> str | None:
        """Query Radarr and Sonarr APIs to find the IMDb ID of the video.

        This method attempts to retrieve the IMDb ID based on the video file's name by utilizing external APIs for Radarr and Sonarr as sources. It first queries Radarr API and checks if the response contains the movie information with the IMDb ID. If found, it returns the IMDb ID.

        If not found, it then queries Sonarr API and checks if the response contains the series information with the IMDb ID. If found, it returns the IMDb ID. If no IMDb ID is found from either API, it returns None.

        Returns:
            str | None: The IMDb ID if found, otherwise None.
        """
        response = query_radarr(self.name)
        if response and "movie" in response and "imdbId" in response["parsedMovieInfo"]:
            return response["movie"]["imdbId"]

        response = query_sonarr(self.name)
        if response and "series" in response and "imdbId" in response["series"]:
            return response["series"]["imdbId"]

        return None

    def _run_ffmpeg(
        self,
        command: list[str],
        title: str,
        suffix: str | None = None,
        step: str | None = None,
    ) -> Path:
        """Execute an ffmpeg command.

        Run the provided ffmpeg command, showing progress and logging information. Determine input and output paths, and manage temporary files related to the operation.

        Args:
            command (list[str]): The ffmpeg command to execute.
            title (str): Title for logging the process.
            suffix (str | None, optional): Suffix for the output file. Use when creating a new container mime type. Defaults to None.
            step (str | None, optional): Step name for file naming. Used when creating a new temporary file. Defaults to None.

        Returns:
            Path: Path to the output file generated by the ffmpeg command.
        """
        input_path = self.temp_file.latest_temp_path()
        output_path = self.temp_file.new_tmp_path(suffix=suffix, step_name=step)

        cmd: list[str] = ["ffmpeg", *FFMPEG_PREPEND, "-i", str(input_path)]
        cmd.extend(command)
        cmd.extend([*FFMPEG_APPEND, str(output_path)])

        pp.trace(f"RUN FFMPEG:\n{' '.join(cmd)}")

        if settings.dryrun:
            console.rule(f"{title} (dry run)")
            markdown_command = Markdown(f"```console\n{' '.join(cmd)}\n```")
            console.print(markdown_command)
            return output_path

        # Run ffmpeg
        ff = FfmpegProgress(cmd)

        with Progress(transient=True) as progress:
            task = progress.add_task(f"{title}…", total=100)
            for complete in ff.run_command_with_progress():
                progress.update(task, completed=complete)

        pp.info(f"{SYMBOL_CHECK} {title}")

        # Set current temporary file and return path
        self.temp_file.created_temp_file(output_path)
        return output_path

    def cleanup(self) -> None:
        """Cleanup temporary files created during video processing.

        Remove all temporary files and directories associated with this VideoFile instance. This includes cleaning up any intermediate files generated during processing.
        """
        self.temp_file.clean_up()

    def clip(self, start: str, duration: str) -> Path:
        """Clip a segment from the video.

        Extract a specific portion of the video based on the given start time and duration. Utilize ffmpeg to perform the clipping operation.

        Args:
            start (str): Start time of the clip.
            duration (str): Duration of the clip.

        Returns:
            Path: Path to the clipped video file.
        """
        # Build ffmpeg command
        ffmpeg_command: list[str] = ["-ss", start, "-t", duration, "-map", "0", "-c", "copy"]

        # Run ffmpeg
        return self._run_ffmpeg(ffmpeg_command, title="Clip video", step="clip")

    def convert_to_h265(self) -> Path:
        """Convert the video to H.265 codec format.

        Check if conversion is necessary and perform it if so. This involves calculating the bitrate, building the ffmpeg command, and running it. Return the path to the converted video or the original video if conversion isn't needed.

        Returns:
            Path: Path to the converted or original video file.
        """
        input_path = self.temp_file.latest_temp_path()

        # Get ffprobe probe
        probe = self._get_probe()
        video_stream = [  # noqa: RUF015
            stream
            for stream in probe.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            pp.error("No video stream found")
            return input_path

        # Return if video is already H.265
        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "H265 ENCODE: Video already H.265 or VP9. Run with `--force` to re-encode. Skipping",
            )
            return input_path

        # Calculate Bitrate
        # ############################
        # Check if duration info is filled, if so times it by 0.0166667 to get time in minutes.
        # If not filled then get duration of stream 0 and do the same.
        stream_duration = float(probe.duration) or float(video_stream.duration)
        if not stream_duration:
            pp.error("Could not calculate video duration")
            return input_path

        duration = stream_duration * 0.0166667

        # Work out currentBitrate using "Bitrate = file size / (number of minutes * .0075)"
        # Used from here https://blog.frame.io/2017/03/06/calculate-video-bitrates/

        stat = input_path.stat()
        pp.trace(f"File size: {stat}")
        file_size_megabytes = stat.st_size / 1000000

        current_bitrate = int(file_size_megabytes / (duration * 0.0075))
        target_bitrate = int(file_size_megabytes / (duration * 0.0075) / 2)
        min_bitrate = int(current_bitrate * 0.7)
        max_bitrate = int(current_bitrate * 1.3)

        # Build FFMPEG Command
        command: list[str] = ["-map", "0", "-c:v", "libx265"]
        # Create bitrate command
        command.extend(
            [
                "-b:v",
                f"{target_bitrate}k",
                "-minrate",
                f"{min_bitrate}k",
                "-maxrate",
                f"{max_bitrate}k",
                "-bufsize",
                f"{current_bitrate}k",
            ],
        )

        # Copy audio and subtitles
        command.extend(["-c:a", "copy", "-c:s", "copy"])
        # Run ffmpeg
        return self._run_ffmpeg(command, title="Convert to H.265", step="h265")

    def convert_to_vp9(self) -> Path:
        """Convert the video to the VP9 codec format.

        Verify if conversion is required and proceed with it using ffmpeg. This method specifically targets the VP9 video codec. Return the path to the converted video or the original video if conversion is not necessary.

        Returns:
            Path: Path to the converted or original video file.
        """
        input_path = self.temp_file.latest_temp_path()

        # Get ffprobe probe
        probe = self._get_probe()
        video_stream = [  # noqa: RUF015
            stream
            for stream in probe.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            pp.error("No video stream found")
            return input_path

        # Return if video is already H.265
        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "VP9 ENCODE: Video already H.265 or VP9. Run with `--force` to re-encode. Skipping",
            )
            return input_path

        # Build ffmpeg command
        command: list[str] = [
            "-map",
            "0",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "0",
            "-crf",
            "30",
            "-c:a",
            "libvorbis",
            "-dn",
            "-map_chapters",
            "-1",
        ]

        # Copy subtitles
        command.extend(["-c:s", "copy"])

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Convert to vp9", suffix=".webm", step="vp9")

    def process_streams(self) -> Path:
        """Process the video file according to specified audio and subtitle preferences.

        Execute the necessary steps to process the video file, including managing audio and subtitle streams.  Keep or discard audio streams based on specified languages, commentary preferences, and downmix settings. Similarly, filter subtitle streams based on language preferences and criteria such as keeping commentary or local subtitles. Perform the processing using ffmpeg and return the path to the processed video file.

        Returns:
            Path: Path to the processed video file.
        """
        probe = self._get_probe()

        video_streams = [s for s in probe.streams if s.codec_type == CodecTypes.VIDEO]
        audio_streams = [s for s in probe.streams if s.codec_type == CodecTypes.AUDIO]
        subtitle_streams = [s for s in probe.streams if s.codec_type == CodecTypes.SUBTITLE]

        video_map_command = self._process_video(video_streams)
        audio_map_command, downmix_command = self._process_audio(streams=audio_streams)
        subtitle_map_command = self._process_subtitles(streams=subtitle_streams)

        # Add flags to title
        title_flags = []

        if audio_map_command:
            title_flags.append("drop original audio") if settings.drop_original_audio else None
            title_flags.append("keep commentary") if settings.keep_commentary else None
            title_flags.append("downmix to stereo") if settings.downmix_stereo else None

        if subtitle_map_command:
            title_flags.append(
                "keep subtitles",
            ) if settings.keep_all_subtitles else title_flags.append("drop unwanted subtitles")
            title_flags.append("keep local subtitles") if settings.keep_local_subtitles else None
            title_flags.append("drop local subtitles") if settings.subs_drop_local else None

        title = f"Process file ({', '.join(title_flags)})" if title_flags else "Process file"

        # Run ffmpeg
        return self._run_ffmpeg(
            video_map_command
            + audio_map_command
            + subtitle_map_command
            + ["-c", "copy"]
            + downmix_command,
            title=title,
            step="process",
        )

    def reorder_streams(self) -> Path:
        """Reorder the media streams within the video file.

        Arrange the streams in the video file so that video streams appear first, followed by audio streams, and then subtitle streams. Exclude certain types of video streams like 'mjpeg' and 'png'.

        Returns:
            Path: Path to the video file with reordered streams.

        Raises:
            cappa.Exit: If no video or audio streams are found in the video file.
        """
        probe = self._get_probe()

        video_streams = [
            s
            for s in probe.streams
            if s.codec_type == CodecTypes.VIDEO
            and s.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        ]
        audio_streams = [s for s in probe.streams if s.codec_type == CodecTypes.AUDIO]
        subtitle_streams = [s for s in probe.streams if s.codec_type == CodecTypes.SUBTITLE]

        # Fail if no video or audio streams are found
        if not video_streams:
            pp.error("No video streams found")
            raise cappa.Exit(code=1)
        if not audio_streams:
            pp.error("No audio streams found")
            raise cappa.Exit(code=1)

        # Check if reordering is needed
        reorder = any(
            stream.index != i
            for i, stream in enumerate(video_streams + audio_streams + subtitle_streams)
        )

        if not reorder:
            pp.info(f"{SYMBOL_CHECK} No streams to reorder")
            return self.temp_file.latest_temp_path()

        # Initial command parts
        initial_command = ["-c", "copy"]

        # Build the command list using list comprehension and concatenation
        command = initial_command + [
            item
            for stream_list in [video_streams, audio_streams, subtitle_streams]
            for stream in stream_list
            for item in ["-map", f"0:{stream.index}"]
        ]

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Reorder streams", step="reorder")

    def video_to_1080p(self) -> Path:
        """Convert video resolution to 1080p.

        Scale video dimensions to 1920x1080 while maintaining aspect ratio. Only converts videos larger than 1080p unless forced.

        Returns:
            Path: Path to the converted video file, or original path if no conversion needed
        """
        input_path = self.temp_file.latest_temp_path()

        # Get ffprobe probe
        probe = self._get_probe()

        video_stream = [  # noqa: RUF015
            stream
            for stream in probe.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_type.value not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            pp.error("No video stream found")
            return input_path

        # Return if video is not 4K
        if not settings.force and getattr(video_stream, "width", 0) <= 1920:  # noqa: PLR2004
            pp.info(f"{SYMBOL_CHECK} No convert to 1080p needed")
            return input_path

        # Build ffmpeg command
        command: list[str] = [
            "-filter:v",
            "scale=width=1920:height=-2",
            "-c:a",
            "copy",
            "-c:s",
            "copy",
        ]

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Convert to 1080p", step="1080p")

    def as_stream_table(self) -> Table:
        """Format video stream information as a Rich table.

        Create a formatted table showing details about video, audio and subtitle streams for display in the terminal.

        Returns:
            Table: Rich table containing stream information
        """
        probe = self._get_probe()
        return probe.as_table()

    def ffprobe_json(self) -> dict:
        """Return the ffprobe json response."""
        return self._get_probe().json_data
