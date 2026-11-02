"""Native absolute paths stay inside authorized roots, including link checks."""
import os
from pathlib import Path
import pytest
from research_agent.core.config import Settings
from research_agent.rag.materials import MaterialResolver

def resolver(tmp_path):
    root=tmp_path/'papers';root.mkdir()
    (root/'真实材料.pdf').write_bytes(b'%PDF fixture')
    return MaterialResolver(Settings(tmp_path/'workspace',tmp_path/'output',material_roots=[root])),root

@pytest.mark.parametrize('pdf_only',[False,True])
@pytest.mark.parametrize('form',['native','forward'])
def test_authorized_native_file_can_be_resolved(tmp_path,pdf_only,form):
    r,root=resolver(tmp_path);p=root/'真实材料.pdf'
    name=str(p) if form=='native' else p.as_posix()
    assert r.resolve([name],pdf_only=pdf_only)[0].path==p

@pytest.mark.parametrize('name',[r'..\outside.pdf',r'C:relative.pdf',r'\\?\C:\papers\x.pdf',r'\\.\C:\papers\x.pdf'])
def test_ambiguous_relative_and_device_paths_rejected(tmp_path,name):
    r,_=resolver(tmp_path)
    with pytest.raises(ValueError):r.resolve([name])

def test_outside_and_traversal_are_rejected(tmp_path):
    r,root=resolver(tmp_path);outside=tmp_path/'outside.pdf';outside.write_bytes(b'%PDF private fixture')
    for path in (outside,root/'../outside.pdf'):
        with pytest.raises(ValueError):r.resolve([str(path)])

def test_native_alternate_stream_rejected(tmp_path):
    r,root=resolver(tmp_path)
    with pytest.raises(ValueError):r.resolve([str(root/'真实材料.pdf')+':stream'])

def test_real_directory_link_cannot_escape_root(tmp_path):
    r,root=resolver(tmp_path);outside=tmp_path/'external';outside.mkdir();(outside/'x.pdf').write_bytes(b'%PDF fixture')
    link=root/'linked'
    if os.name=='nt':
        import _winapi
        _winapi.CreateJunction(str(outside),str(link))
        assert link.is_junction()
    else:link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlinks|junctions'):
        r.resolve([str(link/'x.pdf')])
