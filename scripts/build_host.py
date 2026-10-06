"""Build an Apple Silicon Gather Host app; source/models/session data are separate."""
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
DIST=ROOT/'dist';DIST.mkdir(exist_ok=True)
CACHE=DIST/'downloads';CACHE.mkdir(exist_ok=True)
version='0.12.17';filename='uv-aarch64-apple-darwin.tar.gz'
release=json.load(urllib.request.urlopen('https://api.github.com/repos/astral-sh/uv/releases/tags/'+version))
asset=next(a for a in release['assets'] if a['name']==filename)
digest=asset['digest'].removeprefix('sha256:');archive=CACHE/filename
if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest()!=digest:
    with urllib.request.urlopen(asset['browser_download_url']) as stream,archive.open('wb') as output:shutil.copyfileobj(stream,output)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==digest
app=DIST/'Gather Host.app'
if app.exists():shutil.rmtree(app)
contents=app/'Contents';resources=contents/'Resources';binary=contents/'MacOS';binary.mkdir(parents=True);(resources/'bin').mkdir(parents=True)
with tarfile.open(archive) as tar:
    member=next(m for m in tar.getmembers() if m.name.endswith('/uv') and m.isfile())
    with tar.extractfile(member) as stream,(resources/'bin/uv').open('wb') as output:shutil.copyfileobj(stream,output)
(resources/'bin/uv').chmod(0o755)
for source in ROOT.glob('*.py'):shutil.copy2(source,resources/source.name)
for folder in ('public','host'):shutil.copytree(ROOT/folder,resources/folder)
shutil.copy2(ROOT/'requirements.txt',resources/'requirements.txt')
shutil.copy2(ROOT/'THIRD_PARTY.md',resources/'THIRD_PARTY.md')
for license_name in ('LICENSE-MIT','LICENSE-APACHE'):
    (resources/'bin'/('uv-'+license_name)).write_bytes(urllib.request.urlopen('https://raw.githubusercontent.com/astral-sh/uv/'+version+'/'+license_name).read())
plist={'CFBundleName':'Gather Host','CFBundleDisplayName':'Gather Host','CFBundleIdentifier':'church.citizens.gather.host','CFBundleExecutable':'GatherHost','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'1','LSMinimumSystemVersion':'14.0','NSLocalNetworkUsageDescription':'Gather shares translated sermon audio with listeners on your church network.','CFBundleURLTypes':[{'CFBundleURLName':'Gather Host','CFBundleURLSchemes':['gather']}],'NSHighResolutionCapable':True}
(contents/'Info.plist').write_bytes(plistlib.dumps(plist))
subprocess.run(['xcrun','swiftc',str(ROOT/'host/GatherHost.swift'),'-o',str(binary/'GatherHost'),'-target','arm64-apple-macos14.0','-framework','AppKit'],check=True)
subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
output=DIST/'Gather-Host-Mac.zip'
subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(app),str(output)],check=True)
checksum=hashlib.sha256(output.read_bytes()).hexdigest()
(DIST/'SHA256SUMS').write_text(checksum+'  Gather-Host-Mac.zip\n')
print(json.dumps({'file':str(output),'bytes':output.stat().st_size,'sha256':checksum}))
