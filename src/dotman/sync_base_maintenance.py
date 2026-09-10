"""Selected-policy Base cleanup before real Push planning runs Guards."""

from dataclasses import replace
import os

from dotman import planning
from dotman.sync_base_store import DATABASE_FILE_NAME
from dotman.sync_directory import census_directory, child_metadata
from dotman.sync_observation import _discard_ineligible_bases, _identity


def discard_push_ineligible_bases(
    context: planning.PlanningContext,
    selected_inputs: list[planning.PackagePlanningInput],
) -> None:
    """Caller holds the manager lock; all static ownership/conflict checks passed."""
    inputs = {}
    for item in selected_inputs:
        directory = context.tracked_state.state_root / 'repos' / item.repo.config.state_key
        try:
            exists = any(name.startswith(DATABASE_FILE_NAME) for name in os.listdir(directory))
        except FileNotFoundError:
            exists = False
        if not exists:
            continue
        for metadata in item.target_metadata:
            if metadata.probe_command is not None:
                continue
            identity = _identity(metadata)
            directory = metadata.target.target_type == "directory" or (
                metadata.target.target_type is None
                and (metadata.repo_path.is_dir() or metadata.live_path.is_dir())
            )
            if directory:
                # Census controls keep excluded/failed children opaque. No
                # missing stored child is guessed into this selected workset.
                census = census_directory(
                    metadata,
                    follow_live_directories=context.config.dir_symlink_mode == "follow",
                )
                for relative, failures in census.entries:
                    if relative and not failures:
                        inputs[replace(identity, child_path=relative)] = (
                            item, child_metadata(metadata, relative),
                        )
            else:
                inputs[identity] = (item, metadata)
    _discard_ineligible_bases(context, inputs, preview=False)
