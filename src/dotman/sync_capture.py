"""Lazy reverse projection from a Sync unit's frozen endpoint evidence."""

from dotman.capture import BUILTIN_PATCH_CAPTURE, CaptureError, apply_review_patch
from dotman.command_runtime import CommandRuntime
from dotman.projection import TargetMetadata, project_frozen_file
from dotman.sync_base_store import FilePresent, Missing, DirectoryChildPresent, SyncBasePayload
from dotman.sync_observation import Observation


def capture_observation(
    observation: Observation,
    *,
    metadata: TargetMetadata,
    context: dict,
    command_runtime: CommandRuntime,
    reuse_comparison: bool = True,
) -> SyncBasePayload:
    if isinstance(observation.live, Missing):
        return Missing()
    if observation.live is None or observation.repository is None:
        raise ValueError("Capture requires frozen endpoints")
    # A configured Capture comparison already produced this exact projection
    # during Observation; reviewing it must not run the provider a second time.
    if observation.compare_live == "capture" and reuse_comparison:
        if observation.comparison_live is None:
            raise ValueError("Capture comparison evidence is missing")
        return observation.comparison_live

    def project(repository, view, *, repo_side):
        return project_frozen_file(
            command_runtime, metadata=metadata, context=context,
            repository=repository, live=observation.live.content,
            view=view, repo_side=repo_side,
        )

    repository = (
        observation.repository.content
        if isinstance(observation.repository, (FilePresent, DirectoryChildPresent)) else None
    )
    if metadata.capture_command == BUILTIN_PATCH_CAPTURE:
        if not all(isinstance(state, (FilePresent, DirectoryChildPresent)) for state in (
            observation.repository, observation.comparison_repository,
            observation.comparison_live,
        )):
            raise CaptureError(observation.repository_path, "patch Capture requires present repository and comparison states")
        candidate = apply_review_patch(
            repository, observation.comparison_repository.content,
            observation.comparison_live.content,
            repo_path=observation.repository_path,
        )
        if project(candidate, observation.compare_repo, repo_side=True) != observation.comparison_live.content:
            raise CaptureError(observation.repository_path, "captured bytes do not match the review live bytes")
        return (DirectoryChildPresent(candidate, observation.live.executable)
                if isinstance(observation.live, DirectoryChildPresent) else FilePresent(candidate))
    captured = project(repository, "capture", repo_side=False)
    return Missing() if captured is None else (
        DirectoryChildPresent(captured, observation.live.executable)
        if isinstance(observation.live, DirectoryChildPresent) else FilePresent(captured)
    )
