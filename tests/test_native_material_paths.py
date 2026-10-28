"""An absolute native path must never broaden material authorization."""
import os
from pathlib import Path

import pytest

from research_agent.core.config import Settings
from research_agent.rag.materials import MaterialResolver, MaterialError


def test_network_material_path_is_rejected_without_opening_it(tmp_path):
    library = tmp_path / 'papers'
    library.mkdir()
    resolver = MaterialResolver(Settings(tmp_path, tmp_path / 'out', 'directory', [library]))
    with pytest.raises(MaterialError, match='network'):
        resolver.resolve([r'\\untrusted-server\private\paper.pdf'])


@pytest.mark.skipif(os.name != 'nt', reason='Windows native drive paths')
def test_windows_absolute_path_is_confined_to_explicit_material_root(tmp_path):
    library = tmp_path / 'papers'
    library.mkdir()
    (library / 'valid.pdf').write_bytes(b'%PDF locator-only fixture')
    outside = tmp_path / 'private.pdf'
    outside.write_bytes(b'%PDF unauthorized fixture')
    resolver = MaterialResolver(Settings(tmp_path, tmp_path / 'out', 'directory', [library]))
    assert resolver.resolve([str(library / 'valid.pdf')])[0].filename == 'valid.pdf'
    with pytest.raises(MaterialError, match='outside authorized'):
        resolver.resolve([str(outside)])
