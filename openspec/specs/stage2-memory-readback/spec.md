# stage2-memory-readback Specification

## Purpose
Define the Stage 2 memory read-back contract: wake-up brief injection at
session start across claude / codex / copilot, install-independent hook
bootstrap, hybrid project resolution from the session working directory,
and installer wiring for the codex SessionStart hook.
## Requirements
### Requirement: Wake-up brief read-back injection

On session start, the memory wake-up hook SHALL resolve the session's project and return a concise **orientation** as injected context: a short note that memory is active for the project (with active note count) and that task-relevant memories will surface per-prompt as a shortlist whose listed absolute paths the agent can Read to consume. The SessionStart brief SHALL NOT dump the project MOC or a large recent-slices list, and SHALL NOT prepend a 16-hex citation preamble. When the project cannot be resolved or has no memory, the hook SHALL return empty context. The hook SHALL NOT block, fail, or otherwise disrupt the agent session.

#### Scenario: Known project yields a concise orientation
- **WHEN** a session starts in a directory that resolves to a project with existing knowledge atoms
- **THEN** the hook returns injected context that is a short orientation (memory active, note count, per-prompt shortlist + Read-to-consume hint) and does NOT contain a full project MOC dump or a 16-hex citation preamble

#### Scenario: Unknown or empty project yields empty context
- **WHEN** a session starts in a directory that resolves to no project, or to a project with no atoms
- **THEN** the hook returns empty injected context and the session proceeds normally

### Requirement: Install-independent hook bootstrap

The memory hooks SHALL import the `paulshaclaw` package from a real installed/source package and MUST NOT be shadowed by the memory data directory. A hook MAY add a directory to `sys.path` only when that directory contains a real `paulshaclaw` package (a `paulshaclaw/__init__.py` file), and MUST NOT add the memory data root (which contains only data layers such as `hooks/`, `inbox/`, `knowledge/`).

#### Scenario: Deployed hook resolves the installed package, not the data-dir shadow
- **WHEN** a deployed hook (whose resolved path sits under the memory data directory) runs and computes a candidate repo root that points at the data directory
- **THEN** the hook does not add that data directory to `sys.path`, resolves `paulshaclaw.memory.importer` and `paulshaclaw.memory.wakeup` from the installed package, and builds the brief without an import error

#### Scenario: Running a hook straight from the repo still works
- **WHEN** a hook is run directly from the source repository checkout
- **THEN** the repo root (which contains `paulshaclaw/__init__.py`) is added to `sys.path` and imports resolve to the source package

### Requirement: Hybrid project resolution

`resolve_project` SHALL determine a project slug from the session's working directory using this precedence: (1) a configured project root match yields the configured canonical name; otherwise (2) when inside a git repository with a remote, the slug is the normalized `owner/repo`; otherwise (3) when inside a git repository without a remote, the slug is the repository directory name; otherwise (4) when not in a repository, the slug is the working-folder name. When the working directory's parent contains two or more git repositories, the slug SHALL be a tree path that prefixes the parent-workspace name. Resolution SHALL also populate provenance with the detected git remote. Git detection SHALL be best-effort and MUST degrade (never raise) when git information is unavailable.

#### Scenario: Repo with a remote resolves to owner/repo
- **WHEN** the working directory is inside a git repository that has an `origin` remote
- **THEN** the project slug is the normalized `owner/repo` and provenance records that remote

#### Scenario: Directory without a repository resolves to the working-folder name
- **WHEN** the working directory is not inside any git repository
- **THEN** the project slug is the working-folder name

#### Scenario: Multi-repo workspace resolves to a tree path
- **WHEN** the working directory's parent holds two or more git repositories
- **THEN** the project slug is a tree path prefixed by the parent-workspace name

#### Scenario: Git unavailable degrades instead of failing
- **WHEN** git information cannot be obtained for the working directory
- **THEN** resolution falls back to the working-folder name and does not raise

### Requirement: Read-back coverage for claude, copilot, and codex

Wake-up brief injection SHALL be available for the claude-code, copilot-cli, and codex agents. The codex read-back SHALL be delivered by a `SessionStart` hook that emits the same injected-context contract used by claude and copilot. Capture (write-path) behaviour for all agents SHALL remain unchanged.

#### Scenario: codex session receives an injected brief
- **WHEN** a codex session starts in a directory that resolves to a project with knowledge atoms
- **THEN** the codex `SessionStart` hook emits the brief as injected context

#### Scenario: Capture write-path is unchanged
- **WHEN** the change is deployed
- **THEN** the capture hooks and the importer/dream pipeline continue to operate exactly as before

### Requirement: Hooks are crash-safe

Every memory hook SHALL be fail-safe: on any error it MUST write a warning to the memory hook log, emit empty injected context, and exit zero. A hook MUST NOT propagate an exception or non-zero exit that could disrupt the agent session.

#### Scenario: Hook error does not disrupt the session
- **WHEN** a wake-up hook encounters any error while resolving the project or building the brief
- **THEN** it logs the warning, emits empty injected context, exits zero, and the agent session is unaffected

