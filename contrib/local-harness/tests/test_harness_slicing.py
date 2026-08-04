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


def test_slice_prompt_by_fragments_empty_indices_failsafe():
    prompt = _make_sample_prompt(5)
    # Empty indices should fail-safe return original prompt
    sliced = harness.slice_prompt_by_fragments(prompt, indices=set(), neighbor=1)
    assert sliced == prompt


def test_slice_prompt_by_fragments_out_of_bounds_failsafe():
    prompt = _make_sample_prompt(5)
    # Indices completely out of bounds should fail-safe return original prompt
    sliced = harness.slice_prompt_by_fragments(prompt, indices={99, 100}, neighbor=1)
    assert sliced == prompt


def test_slice_prompt_by_fragments_no_fragments_failsafe():
    prompt = "# Task: simple prompt with no fragment markers\n## Output\nReturn JSON."
    sliced = harness.slice_prompt_by_fragments(prompt, indices={0}, neighbor=1)
    assert sliced == prompt
