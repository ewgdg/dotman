"""Isolated Editor invocation; only declared repository sources leave the workspace."""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import shutil
import tempfile

from dotman.reconcile import run_basic_reconcile, resolve_editor_additional_sources
from dotman.templates import discover_template_file_dependencies
from dotman.sync_base_store import FilePresent, Missing


class EditorCommandFailed(Exception):
    """The Editor could not be launched."""


@dataclass(frozen=True)
class AdditionalEdit:
    path: Path
    before: bytes
    candidate: bytes


@dataclass(frozen=True)
class EditorOutput:
    exit_code: int
    repository: FilePresent | Missing
    additional: tuple[AdditionalEdit, ...] = ()


def freeze_additional_sources(*, metadata, repo_root, primary_paths):
    package_root = Path(metadata.command_env['DOTMAN_PACKAGE_ROOT'])
    sources = resolve_editor_additional_sources(
        editor=metadata.editor, additional_sources=metadata.additional_sources,
        additional_source_entries=metadata.additional_source_entries,
        additional_sources_root=metadata.additional_sources_root, package_root=package_root,
    )
    if (metadata.editor.type == 'jinja' or metadata.render_command == 'jinja') and metadata.repo_path.exists():
        sources.extend(discover_template_file_dependencies(metadata.repo_path))
    sources = list(dict.fromkeys(path for path in sources if path != metadata.repo_path))
    if any(path in primary_paths for path in sources):
        raise ValueError('An Additional Source cannot be another Sync Unit Primary Source')
    for path in [metadata.repo_path, *sources]:
        path.relative_to(repo_root)
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('Editor sources must be regular repository paths')
    return {path: path.read_bytes() for path in sources}


@contextmanager
def repository_workspace(*, metadata, repo_root, preimages):
    with tempfile.TemporaryDirectory(prefix='dotman-sync-editor-') as directory:
        root = Path(directory) / 'repository'
        # Keep provider-relative dependencies isolated; overwrite declared inputs
        # from Observation-time bytes rather than adopting external edits.
        shutil.copytree(repo_root, root, symlinks=True, ignore=shutil.ignore_patterns('.git'))
        def staged(path):
            return root / path.relative_to(repo_root)
        for path, content in preimages.items():
            destination = staged(path)
            if destination.is_symlink() or destination.resolve() != destination:
                raise ValueError('Editor sources must be regular repository paths')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        env = {key: value.replace(str(repo_root), str(root)) for key, value in metadata.command_env.items()}
        yield replace(metadata, repo_path=staged(metadata.repo_path),
                      command_cwd=staged(metadata.command_cwd), command_env=env), staged, Path(directory)


def edit_sources(*, observation, proposal, metadata, repo_root, preimages, additional=()):
    initial = proposal.repository if proposal is not None else observation.repository
    sources = tuple(preimages)
    retained = {change.path: change for change in additional}
    with repository_workspace(metadata=metadata, repo_root=repo_root, preimages=preimages) as (staged_metadata, staged, directory):
        primary = staged(metadata.repo_path)
        if primary.is_symlink() or primary.resolve() != primary:
            raise ValueError('Editor sources must be regular repository paths')
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.write_bytes(initial.content if isinstance(initial, FilePresent) else b'')
        for path in sources:
            staged(path).write_bytes(retained[path].candidate if path in retained else preimages[path])
        live = directory / 'live'
        live.write_bytes(observation.live.content if isinstance(observation.live, FilePresent) else b'')
        try:
            result = run_basic_reconcile(
                repo_path=str(primary), live_path=str(live),
                additional_sources=[str(staged(path)) for path in sources],
                editor=metadata.editor.run if metadata.editor.type is None else None,
                assume_yes=True, review_repo_bytes=observation.comparison_repository.content
                if isinstance(observation.comparison_repository, FilePresent) else b'',
                review_live_bytes=observation.comparison_live.content
                if isinstance(observation.comparison_live, FilePresent) else b'',
                editor_env=staged_metadata.command_env, editor_cwd=staged_metadata.command_cwd,
                editor_io=metadata.editor.io, editor_elevation=metadata.editor.elevation,
                return_result=True, quiet=True,
            )
        except InterruptedError:
            # Runtime cancellation is an OSError subclass, not a provider failure.
            raise
        except OSError as exc:
            raise EditorCommandFailed(str(exc)) from exc
        content = primary.read_bytes()
        repository = Missing() if isinstance(initial, Missing) and content == b'' else FilePresent(content)
        changes = tuple(AdditionalEdit(path, preimages[path], staged(path).read_bytes())
                        for path in sources if staged(path).read_bytes() != preimages[path])
        return EditorOutput(result.exit_code, repository, changes)
