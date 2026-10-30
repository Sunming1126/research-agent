"""Independent static image overlay/source audit; no image execution."""
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'提交材料/科研助手-0.4.19-20261030'
manifest=json.loads((OUT/'delivery-manifest.json').read_text(encoding='utf-8'))
proof=json.loads((ROOT/'协同开发/research-agent-stability-20261027/docs/testing/2026-10-29-b-stability-closure/candidate-final-source-files.json').read_text(encoding='utf-8'))
expected=json.loads((OUT/'source-sha256.json').read_text(encoding='utf-8'))
application={};syntax=0;matches=0
with tarfile.open(OUT/manifest['image_archive']) as outer:
    saved=json.load(outer.extractfile('manifest.json'))
    assert len(saved)==1
    config_bytes=outer.extractfile(saved[0]['Config']).read()
    assert 'sha256:'+hashlib.sha256(config_bytes).hexdigest()==manifest['image_id']
    cfg=json.loads(config_bytes)
    assert cfg['os']=='linux' and cfg['architecture']=='amd64'
    assert cfg['config']['User']=='agent' and cfg['config']['WorkingDir']=='/app'
    assert cfg['config']['Cmd']==['python','agent.py'] and not cfg['config'].get('Entrypoint')
    assert '8080/tcp' in cfg['config']['ExposedPorts']
    env=dict(value.split('=',1) for value in cfg['config']['Env'])
    assert not env.get('TEAM_ID') and 'RESEARCH_WORKSPACE_TEAM' not in env
    assert env['RESEARCH_WORKSPACE_ROOT']=='/data' and env['RESEARCH_RESULT_ROOT']=='/output'
    for layername in saved[0]['Layers']:
        blob=outer.extractfile(layername).read()
        data=gzip.decompress(blob) if blob[:2]==b'\x1f\x8b' else blob
        with tarfile.open(fileobj=io.BytesIO(data)) as layer:
            members=layer.getmembers()
            if any(member.name=='app/.wh..wh..opq' for member in members):application.clear()
            for member in members:
                path=PurePosixPath(member.name)
                if not str(path).startswith('app/'):continue
                if path.name.startswith('.wh.'):
                    if path.name!='.wh..wh..opq':
                        target=str(path.parent/path.name[4:])
                        application={name:data for name,data in application.items() if name!=target and not name.startswith(target+'/')}
                    continue
                if member.isfile():application[str(path)]=layer.extractfile(member).read()
                elif member.issym() or member.islnk():raise AssertionError('Unexpected app symlink')
    assert not any('d_algorithms' in PurePosixPath(name).parts for name in application)
    assert set(application)=={'app/'+name for name in expected if name.startswith('src/') or name in {'agent.py','README.md','requirements.txt'}}
    for name,data in application.items():
        relative=name[4:]
        assert hashlib.sha256(data).hexdigest()==expected[relative]
        if name.endswith('.py'):
            ast.parse(data,filename=name,feature_version=(3,11));syntax+=1
            assert relative in proof and proof[relative]==hashlib.sha256(data).hexdigest(), relative+' differs from final regression bytes; tracked='+str(relative in proof)
            matches+=1
    with zipfile.ZipFile(OUT/manifest['source_archive']) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():assert hashlib.sha256(archive.read(name)).hexdigest()==expected[name]
report={'kind':'independent_static_archive_audit','source_revision':manifest['source_revision'],
        'application_file_count':len(application),'python_3_11_syntax_files':syntax,
        'exact_python_bytes_match_final_regression':matches,'no_stale_application_files':True,
        'source_zip_readback':True,'official_runtime_entry_retained':True,
        'image_import_and_start_tested':False,'native_harness_execution':False,'real_model_calls':0}
(OUT/'independent-static-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
