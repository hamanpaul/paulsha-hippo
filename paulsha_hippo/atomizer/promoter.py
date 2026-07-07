from __future__ import annotations

from abc import ABC, abstractmethod

from . import slice_frontmatter
from .config import AtomizerConfig
from .slice_frontmatter import Slice
from .splitter import Fragment


class Promoter(ABC):
    """Maps one session's fragments to knowledge slices."""

    @abstractmethod
    def promote(self, fragments: list[Fragment], config: AtomizerConfig) -> list[Slice]:
        ...


class IdentityPromoter(Promoter):
    def promote(self, fragments: list[Fragment], config: AtomizerConfig) -> list[Slice]:
        if isinstance(fragments, Fragment):
            fragments = [fragments]
        return [slice_frontmatter.build(fragment, config) for fragment in fragments]
