import sys
from pathlib import Path

# Add contrib/local-harness directory to sys.path so harness can be imported
HARNESS_DIR = Path(__file__).resolve().parent.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

import harness


def _make_sample_prompt(num_fragments: int = 8) -> str:
    parts = [
        "# Task: atomize session",
        "",
        "## Known projects",
        "paulsha-hippo, _unknown",
        "",
        "## Session fragments to atomize",
    ]
    for i in range(num_fragments):
        parts.append(f"[fragment {i}]")
        parts.append(f"Content of fragment {i}. " * 20)
        parts.append("")
    parts.append("## Output")
    parts.append("Return ONLY a canonical JSON object.")
    return "\n".join(parts)


def test_slice_prompt_by_fragments_basic():
    prompt = _make_sample_prompt(8)
    sliced = harness.slice_prompt_by_fragments(prompt, indices={3}, neighbor=1)
    
    # Preamble and Output must be retained
    assert "# Task: atomize session" in sliced
    assert "## Session fragments to atomize" in sliced
    assert "## Output" in sliced
    assert "Return ONLY a canonical JSON object." in sliced

    # Targeted fragments (3 +- 1 => 2, 3, 4) must be included
    assert "[fragment 2]" in sliced
    assert "[fragment 3]" in sliced
    assert "[fragment 4]" in sliced

    # Non-targeted fragments must be excluded
    assert "[fragment 0]" not in sliced
    assert "[fragment 1]" not in sliced
    assert "[fragment 5]" not in sliced
    assert "[fragment 6]" not in sliced
    assert "[fragment 7]" not in sliced

    # Sliced length must be significantly smaller than original
    assert len(sliced) < len(prompt) * 0.6


def test_slice_prompt_by_fragments_neighbor_zero():
    prompt = _make_sample_prompt(5)
    sliced = harness.slice_prompt_by_fragments(prompt, indices={2}, neighbor=0)

    assert "[fragment 2]" in sliced
    assert "[fragment 1]" not in sliced
    assert "[fragment 3]" not in sliced


def test_slice_prompt_by_fragments_empty_indices_failsafe(capsys):
    prompt = _make_sample_prompt(5)
    # Empty indices should fail-safe return original prompt AND warn on stderr
    sliced = harness.slice_prompt_by_fragments(prompt, indices=set(), neighbor=1)
    assert sliced == prompt
    err = capsys.readouterr().err
    assert "warning: input slicing" in err
    assert "falling back to full prompt" in err


def test_slice_prompt_by_fragments_out_of_bounds_failsafe(capsys):
    prompt = _make_sample_prompt(5)
    # Indices completely out of bounds should fail-safe return original prompt
    sliced = harness.slice_prompt_by_fragments(prompt, indices={99, 100}, neighbor=1)
    assert sliced == prompt
    err = capsys.readouterr().err
    assert "warning: input slicing" in err
    assert "falling back to full prompt" in err


def test_slice_prompt_by_fragments_no_fragments_failsafe(capsys):
    prompt = "# Task: simple prompt with no fragment markers\n## Output\nReturn JSON."
    sliced = harness.slice_prompt_by_fragments(prompt, indices={0}, neighbor=1)
    assert sliced == prompt
    err = capsys.readouterr().err
    assert "warning: input slicing" in err
    assert "falling back to full prompt" in err


def test_slice_prompt_single_fragment_hit():
    prompt = _make_sample_prompt(1)
    sliced = harness.slice_prompt_by_fragments(prompt, indices={0}, neighbor=1)

    assert "# Task: atomize session" in sliced
    assert "[fragment 0]" in sliced
    assert "Content of fragment 0." in sliced
    assert "## Output" in sliced
    assert "Return ONLY a canonical JSON object." in sliced


def test_slice_prompt_single_fragment_miss_failsafe(capsys):
    prompt = _make_sample_prompt(1)
    # neighbor=0 so index 5 cannot reach fragment 0 -> fail-safe full prompt
    sliced = harness.slice_prompt_by_fragments(prompt, indices={5}, neighbor=0)
    assert sliced == prompt
    err = capsys.readouterr().err
    assert "warning: input slicing" in err
    assert "falling back to full prompt" in err


def _make_multipart_prompt() -> str:
    parts = [
        "# Task: atomize session",
        "",
        "## Session fragments to atomize",
        "[fragment 0]",
        "Body of fragment 0. " * 10,
        "",
        "[fragment 1 part 1/2]",
        "First half of fragment 1. " * 10,
        "",
        "[fragment 1 part 2/2]",
        "Second half of fragment 1. " * 10,
        "",
        "[fragment 5]",
        "Body of fragment 5. " * 10,
        "",
        "## Output",
        "Return ONLY a canonical JSON object.",
    ]
    return "\n".join(parts)


def test_slice_prompt_multipart_selected_keeps_all_parts():
    prompt = _make_multipart_prompt()
    sliced = harness.slice_prompt_by_fragments(prompt, indices={1}, neighbor=0)

    # Both parts of fragment 1 share one index and must be kept together
    assert "[fragment 1 part 1/2]" in sliced
    assert "First half of fragment 1." in sliced
    assert "[fragment 1 part 2/2]" in sliced
    assert "Second half of fragment 1." in sliced
    # Non-selected fragments must be dropped (neighbor=0, 5 is out of window)
    assert "[fragment 5]" not in sliced
    assert "Body of fragment 5." not in sliced


def test_slice_prompt_multipart_unselected_drops_all_parts():
    prompt = _make_multipart_prompt()
    sliced = harness.slice_prompt_by_fragments(prompt, indices={5}, neighbor=0)

    assert "[fragment 5]" in sliced
    assert "Body of fragment 5." in sliced
    # Both parts of unselected fragment 1 must be dropped together
    assert "[fragment 1 part 1/2]" not in sliced
    assert "First half of fragment 1." not in sliced
    assert "[fragment 1 part 2/2]" not in sliced
    assert "Second half of fragment 1." not in sliced


def _make_prompt_with_fake_markers() -> str:
    """Fragment bodies quoting marker-lookalike lines (indented / uppercase /
    unclosed) and a `## Output`-prefixed body line: none of these may act as a
    block boundary, and no input line may be silently dropped."""
    parts = [
        "# Task: atomize session",
        "",
        "## Session fragments to atomize",
        "[fragment 0]",
        "Discussing the prompt format:",
        "    [fragment 7] quoted example inside a session transcript",
        "and also [Fragment 3] uppercase mention mid-line.",
        "[fragment 9 without closing bracket",
        "## Output formats considered: JSON, YAML",
        "still body of fragment 0.",
        "",
        "[fragment 1]",
        "Body of fragment 1. " * 10,
        "",
        "## Output",
        "Return ONLY a canonical JSON object.",
    ]
    return "\n".join(parts)


def test_fake_markers_in_body_do_not_split_fragments():
    prompt = _make_prompt_with_fake_markers()
    sliced = harness.slice_prompt_by_fragments(prompt, indices={0}, neighbor=0)

    # The whole body of fragment 0 stays attributed to fragment 0
    assert "[fragment 0]" in sliced
    assert "    [fragment 7] quoted example inside a session transcript" in sliced
    assert "[Fragment 3] uppercase mention mid-line." in sliced
    assert "[fragment 9 without closing bracket" in sliced
    assert "## Output formats considered: JSON, YAML" in sliced
    assert "still body of fragment 0." in sliced
    # Real fragment 1 is a separate block and is dropped (neighbor=0)
    assert "Body of fragment 1." not in sliced
    # Real tail retained
    assert "Return ONLY a canonical JSON object." in sliced


def test_fake_markers_in_body_not_selectable_as_fragment():
    prompt = _make_prompt_with_fake_markers()
    # Index 7 exists only as a quoted lookalike inside fragment 0's body; it
    # must NOT be treated as a real block -> fail-safe full prompt.
    sliced = harness.slice_prompt_by_fragments(prompt, indices={7}, neighbor=0)
    assert sliced == prompt


def test_marker_line_after_output_tail_is_preserved():
    # A marker-shaped line inside the tail must never be silently dropped.
    parts = [
        "# Task: atomize session",
        "",
        "[fragment 0]",
        "Body of fragment 0.",
        "",
        "[fragment 1]",
        "Body of fragment 1.",
        "",
        "## Output",
        "Return ONLY a canonical JSON object.",
        "[fragment 0]",
        "e.g. cite markers like the line above verbatim.",
    ]
    prompt = "\n".join(parts)
    sliced = harness.slice_prompt_by_fragments(prompt, indices={1}, neighbor=0)

    assert "Body of fragment 1." in sliced
    assert "Body of fragment 0." not in sliced
    # Tail preserved verbatim, including the marker-shaped line
    assert "Return ONLY a canonical JSON object.\n[fragment 0]\ne.g. cite markers like the line above verbatim." in sliced
