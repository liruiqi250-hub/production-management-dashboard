"""维护者把固定源码构建产物制成可校验的媒体依赖档；不读取业务资料。"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()

def archive(path, files):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            target.writestr(info, data)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--ffmpeg-bin',type=Path,required=True)
    parser.add_argument('--sndfile-dll',type=Path,required=True)
    parser.add_argument('--ffmpeg-source',type=Path,required=True)
    parser.add_argument('--sndfile-source',type=Path,required=True)
    args=parser.parse_args()
    cache=args.cache.resolve(); notices=ROOT/'dependencies/media'
    release_tag='v'+(ROOT/'VERSION').read_text(encoding='utf-8-sig').strip()
    ffmpeg=subprocess.check_output([str(args.ffmpeg_bin/'ffmpeg.exe'),'-version'],encoding='utf-8')
    if 'ffmpeg version 8.0.1 ' not in ffmpeg or '--enable-gpl' in ffmpeg or '--enable-nonfree' in ffmpeg:
        raise ValueError('FFmpeg固定版本及许可配置不符合要求')
    # 原始源码未作修改；构建配置单独保存。
    sources=[]
    for name,tree,url in (
        ('ffmpeg-8.0.1.tar.xz',args.ffmpeg_source,'https://ffmpeg.org/releases/ffmpeg-8.0.1.tar.xz'),
        ('libsndfile-1.2.2.tar.xz',args.sndfile_source,'https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz')):
        path=cache/name
        with tarfile.open(path,'r:xz') as source:
            for member in source.getmembers():
                if not member.isfile(): continue
                relative=Path(*Path(member.name).parts[1:])
                installed=tree/relative
                if not installed.is_file() or hashlib.sha256(source.extractfile(member).read()).hexdigest()!=digest(installed):
                    raise ValueError('构建源码与原始归档不一致：'+relative.as_posix())
        sources.append(dict(filename=name,url=url,sha256=digest(path),size=path.stat().st_size,source_modifications=[]))
    sherpa_source=cache/'sherpa-onnx-v1.13.6-source.zip'
    sources.append(dict(filename=sherpa_source.name,url='https://codeload.github.com/k2-fsa/sherpa-onnx/zip/refs/tags/v1.13.6',sha256=digest(sherpa_source),size=sherpa_source.stat().st_size,git_commit='1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911'))
    dependencies=json.loads((cache/'sherpa-source-manifest.json').read_text(encoding='utf-8'))
    dependencies+=json.loads((cache/'nested-source-manifest.json').read_text(encoding='utf-8'))
    unique={}
    for item in dependencies:
        path=cache/'sherpa-sources'/item['filename']
        if digest(path)!=item['sha256'] or path.stat().st_size!=item['size']:
            raise ValueError('依赖源码校验失败：'+path.name)
        unique[path.name]=item
    license_records=[]
    for filename,item in unique.items():
        if filename.startswith('onnxruntime-'): continue  # Complete upstream ThirdPartyNotices is included separately.
        path=cache/'sherpa-sources'/filename
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as source:
                content={entry.filename:source.read(entry) for entry in source.infolist()
                         if not entry.is_dir() and entry.file_size<2*1024*1024
                         and re.search(r'^(?:licen[cs]e|copying|copyright|notice)(?:$|[._-])',Path(entry.filename).name,re.I)}
        else:
            with tarfile.open(path,'r:*') as source:
                content={entry.name:source.extractfile(entry).read() for entry in source.getmembers()
                         if entry.isfile() and entry.size<2*1024*1024
                         and re.search(r'^(?:licen[cs]e|copying|copyright|notice)(?:$|[._-])',Path(entry.name).name,re.I)}
        for member,data in sorted(content.items()):
            try: data.decode('utf-8-sig')
            except UnicodeDecodeError: continue
            if b'\x00' in data: continue
            relative=f'third-party/{filename}/{len(license_records):04d}.txt'
            target=notices/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
            license_records.append(dict(source_archive=filename,source_member=member,path=relative,sha256=hashlib.sha256(data).hexdigest()))
    tools=[]
    for name,url in (
        ('llvm-mingw-20261006-ucrt-x86_64.zip','https://github.com/mstorsjo/llvm-mingw/releases/download/20261006/llvm-mingw-20261006-ucrt-x86_64.zip'),
        ('cmake-4.4.4-windows-x86_64.zip','https://github.com/Kitware/CMake/releases/download/v4.4.4/cmake-4.4.4-windows-x86_64.zip'),
        ('make-4.4.1-2-x86_64.pkg.tar.zst','https://repo.msys2.org/msys/x86_64/make-4.4.1-2-x86_64.pkg.tar.zst')):
        path=cache/name
        tools.append(dict(filename=name,url=url,sha256=digest(path),size=path.stat().st_size,distributed=False))
    manifest=dict(schema=1,platform='windows-x64',sources=sources,native_dependencies=list(unique.values()),build_tools=tools,license_files=license_records,
                  ffmpeg_version_configuration=ffmpeg,libsndfile_version='1.2.2',sherpa_git_commit='1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911',onnxruntime_version='1.27.1')
    (notices/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    configuration=next(line.partition('configuration: ')[2] for line in ffmpeg.splitlines() if line.startswith('configuration: '))
    flags=shlex.split(configuration)
    flags=[flag for flag in flags if not flag.startswith('--prefix=')]
    rebuild='''#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 2 ]; then echo "Usage: rebuild_ffmpeg.sh SOURCE_DIR OUTPUT_DIR" >&2; exit 2; fi
cd "$1"
./configure --prefix="$2" '''+shlex.join(flags)+'''
# Use MSYS GNU make (Git Bash/MSYS runtime), not native mingw32-make:
# native Windows command shells truncate long object lists at link time.
"${MSYS_MAKE:-make}" -j8
"${MSYS_MAKE:-make}" install
'''
    (notices/'rebuild_ffmpeg.sh').write_text(rebuild,encoding='utf-8',newline='\n')
    components=[]
    ff_files={'bin/'+p.name:p.read_bytes() for p in args.ffmpeg_bin.iterdir() if p.suffix.lower() in {'.dll','.exe'}}
    for name in ('COPYING.LGPLv2.1','COPYING.LGPLv3','LICENSE.md'):
        ff_files['licenses/'+name]=(args.ffmpeg_source/name).read_bytes()
    for name in ('LLVM-RUNTIME-LICENSE.txt','MinGW-RUNTIME-LICENSE.txt'):
        ff_files['licenses/'+name]=(notices/name).read_bytes()
    ff_files['BUILD_CONFIGURATION.txt']=ffmpeg.encode()
    snd_files={'libsndfile_x64.dll':args.sndfile_dll.read_bytes(),
               'COPYING':(args.sndfile_source/'COPYING').read_bytes(),
               'LLVM-RUNTIME-LICENSE.txt':(notices/'LLVM-RUNTIME-LICENSE.txt').read_bytes(),
               'MinGW-RUNTIME-LICENSE.txt':(notices/'MinGW-RUNTIME-LICENSE.txt').read_bytes()}
    for ident,filename,files,license_name in (
        ('ffmpeg','ffmpeg-8.0.1-lgpl-win64.zip',ff_files,'LGPL-2.1-or-later'),
        ('libsndfile','libsndfile-1.2.2-minimal-win64.zip',snd_files,'LGPL-2.1-or-later')):
        path=cache/filename; archive(path,files)
        components.append(dict(id=ident,archive_filename=filename,archive_sha256=digest(path),archive_bytes=path.stat().st_size,release_tag=release_tag,license=license_name,files={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}))
    companion_files={'sources/'+item['filename']:(cache/item['filename']).read_bytes() for item in sources}
    companion_files.update({'sources/sherpa-dependencies/'+name:(cache/'sherpa-sources'/name).read_bytes() for name in unique})
    companion_files.update({'materials/'+p.relative_to(notices).as_posix():p.read_bytes() for p in notices.rglob('*') if p.is_file()})
    companion=cache/'media-corresponding-sources-ffmpeg8.0.1-sndfile1.2.2-sherpa1.13.6.zip'
    archive(companion,companion_files)
    lock=dict(schema=1,platform='windows-x64',components=components,corresponding_sources=dict(archive_filename=companion.name,archive_sha256=digest(companion),archive_bytes=companion.stat().st_size,release_tag=release_tag))
    (ROOT/'dependencies/media-runtime.lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for path in [cache/item['archive_filename'] for item in components]+[companion]:
        path.with_suffix('.zip.sha256').write_text(f'{digest(path)}  {path.name}\n',encoding='ascii')
    print(json.dumps({'assets':[{**item} for item in components]+[lock['corresponding_sources']]},ensure_ascii=False))

if __name__=='__main__': main()
