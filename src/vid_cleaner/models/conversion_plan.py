"""Plan a single-pass ffmpeg conversion."""

from dataclasses import dataclass, field

from vid_cleaner.constants import CodecTypes

# ffmpeg per-stream options address streams by type-scoped OUTPUT index (`-c:a:1` is the
# second audio stream in the output), which is only knowable once every stream has been
# composed. The plan therefore owns all output-index arithmetic in build_command();
# planners never hard-code output indices.
_TYPE_FLAG = {
    CodecTypes.VIDEO: "v",
    CodecTypes.AUDIO: "a",
    CodecTypes.SUBTITLE: "s",
}


@dataclass
class OutputStream:
    """One stream mapped into the planned output file.

    Attributes:
        source_index (int): Input stream index to map (`-map 0:<source_index>`).
        codec_type (CodecTypes): Stream type, used to scope per-stream options.
        codec (str | None): Encoder name, "copy" to stream-copy, or None to omit the
            codec option entirely so ffmpeg picks the container default.
        stream_filter (str | None): Filter chain for `-filter:<type>:<n>`.
        extra_args (list[str]): Per-stream option templates; `{n}` is replaced with the
            type-scoped output index (e.g. `"-b:a:{n}"`).
        metadata (dict[str, str]): Stream metadata written via `-metadata:s:<type>:<n>`.
    """

    source_index: int
    codec_type: CodecTypes
    codec: str | None = "copy"
    stream_filter: str | None = None
    extra_args: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class PlanAction:
    """One cleaning operation the plan considered, for user-facing reporting.

    Attributes:
        label (str): Human-readable operation name (e.g. "Downmix to stereo").
        applied (bool): Whether the operation actually runs in this pass.
        reason (str | None): Why the operation was skipped, shown only in debug output.
    """

    label: str
    applied: bool
    reason: str | None = None


@dataclass
class ConversionPlan:
    """A composed single-pass ffmpeg conversion for one video file.

    Collect every requested operation (stream selection, downmix, scale, codec change)
    into one command so the file is decoded and written exactly once.

    Attributes:
        streams (list[OutputStream]): Output streams in final order (video, audio,
            subtitles). Mapping order performs any stream reordering for free.
        global_args (list[str]): Output-wide args appended after per-stream options.
        output_suffix (str | None): Container suffix override (e.g. ".webm").
        substeps (list[str]): Result-tree messages describing the applied operations.
        actions (list[PlanAction]): Cleaning operations the plan considered.
    """

    streams: list[OutputStream] = field(default_factory=list)
    global_args: list[str] = field(default_factory=list)
    output_suffix: str | None = None
    substeps: list[str] = field(default_factory=list)
    actions: list[PlanAction] = field(default_factory=list)

    def is_noop(self, stream_count: int) -> bool:
        """Check whether executing this plan would change nothing about the file.

        Args:
            stream_count (int): Number of processable streams in the input file.

        Returns:
            bool: True when every input stream is kept, in original order (identity
                mapping), as a plain copy with no filters, metadata, global args, or
                container change.
        """
        return (
            not self.global_args
            and self.output_suffix is None
            and [stream.source_index for stream in self.streams] == list(range(stream_count))
            and all(
                stream.codec == "copy"
                and not stream.stream_filter
                and not stream.extra_args
                and not stream.metadata
                for stream in self.streams
            )
        )

    def build_command(self) -> list[str]:
        """Build the ffmpeg argument list for this plan.

        Emit every `-map` first (establishing output order), then each stream's options
        addressed by its type-scoped output index, then the global args.

        Returns:
            list[str]: ffmpeg arguments, excluding the input/output paths.
        """
        command: list[str] = []
        for stream in self.streams:
            command.extend(["-map", f"0:{stream.source_index}"])

        type_counts: dict[CodecTypes, int] = {}
        for stream in self.streams:
            index = type_counts.get(stream.codec_type, 0)
            type_counts[stream.codec_type] = index + 1
            flag = _TYPE_FLAG[stream.codec_type]

            if stream.codec is not None:
                command.extend([f"-c:{flag}:{index}", stream.codec])
            if stream.stream_filter:
                command.extend([f"-filter:{flag}:{index}", stream.stream_filter])
            command.extend(arg.format(n=index) for arg in stream.extra_args)
            for key, value in stream.metadata.items():
                command.extend([f"-metadata:s:{flag}:{index}", f"{key}={value}"])

        command.extend(self.global_args)
        return command
