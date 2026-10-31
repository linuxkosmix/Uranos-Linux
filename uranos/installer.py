"""Privileged installer. Only usable from Uranos live media; stdin carries secrets."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import signal
import time
from dataclasses import asdict
from .model import Recipe, DESKTOPS
from .branding import apply_branding

ENV = dict(os.environ, PATH='/usr/bin:/usr/sbin', LC_ALL='C.UTF-8')

def run(*argv, input=None, capture=False):
    if input is None:
        print('+ ' + ' '.join(map(str, argv)), flush=True)
    result = subprocess.run(list(map(str, argv)), input=input, text=True, env=ENV,
                            stdout=subprocess.PIPE if capture else None, stderr=None)
    if result.returncode:
        raise RuntimeError(f'{argv[0]}: код {result.returncode}')
    return result.stdout if capture else ''

def nodes(node):
    yield node
    for child in node.get('children', []):
        yield from nodes(child)

def usable_disk(d):
    return (d.get('type') == 'disk' and not d.get('ro') and int(d.get('size', 0)) >= 16*1024**3
            and all(not any(n.get('mountpoints') or []) and n.get('type') in ('disk', 'part') for n in nodes(d)))

def disks():
    data = subprocess.check_output(['lsblk','--json','--bytes','--paths','-o',
        'NAME,SIZE,MODEL,SERIAL,TYPE,RO,MOUNTPOINTS,MAJ:MIN'], env=ENV, text=True)
    result=[]
    for d in json.loads(data)['blockdevices']:
        if usable_disk(d):
            result.append({'path': d['name'], 'size': int(d['size']), 'model': (d.get('model') or '').strip(),
                           'serial': (d.get('serial') or '').strip(), 'device': d['maj:min']})
    return result

def validate_request(request):
    r=Recipe(**request['recipe']); r.validate()
    if r.desktop != 'KDE Plasma':
        raise ValueError('В этой версии установка проверяется только для KDE Plasma. GNOME и Hyprland пока недоступны для установки.')
    if r.desktop_config.strip():
        raise ValueError('Произвольные настройки DE пока нельзя применять при установке.')
    if request.get('boot_mode') not in ('bios', 'uefi'):
        raise ValueError('Неизвестный режим загрузки.')
    if not r.admin:
        raise ValueError('Первый пользователь должен иметь права администратора.')
    password=request.get('password','')
    if len(password)<8 or any(c in password for c in '\n\r\x00'):
        raise ValueError('Пароль должен содержать минимум 8 символов без управляющих символов.')
    disk=request['disk']
    if request.get('confirm') != 'ERASE '+disk['path']:
        raise ValueError('Не подтверждено стирание выбранного диска.')
    if disk not in disks():
        raise ValueError('Диск изменился, занят или содержит подключённые разделы. Обнови список дисков.')
    return r

def write(root, path, text, mode=0o644):
    f=root/path.lstrip('/'); f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text); f.chmod(mode)

def target_processes(target):
    target=Path(target).resolve()
    found=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name)==os.getpid(): continue
        try:
            links=[proc/'root', proc/'cwd', proc/'exe']+list((proc/'fd').iterdir())
            for link in links:
                try:
                    path=Path(os.readlink(link))
                    if path.is_absolute() and (path==target or target in path.parents):
                        found.append(int(proc.name)); break
                except OSError: pass
        except OSError: pass
    return found

def reap_children():
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0]==0: return
        except ChildProcessError: return

def cleanup_target(target):
    # Helper runs as PID 1 in a private PID/mount namespace. Never signal live desktop processes.
    if os.getpid()!=1:
        raise RuntimeError('Очистка требует изолированного процесса установщика.')
    for sig,timeout in ((signal.SIGTERM,3),(signal.SIGKILL,2)):
        for pid in target_processes(target):
            try:
                fd=os.pidfd_open(pid)
                try:
                    if pid in target_processes(target): signal.pidfd_send_signal(fd,sig)
                finally: os.close(fd)
            except ProcessLookupError: pass
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            reap_children()
            if not target_processes(target): break
            time.sleep(.1)
    error=''
    for attempt in range(5):
        # A previous attempt may already have unmounted some nested mountpoints.
        if not os.path.ismount(target): return
        result=subprocess.run(['umount','--recursive',str(target)],text=True,env=ENV,capture_output=True)
        if result.returncode==0 and not os.path.ismount(target): return
        error=result.stderr.strip()
        time.sleep(.3)
    raise RuntimeError('Не удалось отключить установленную систему: '+error)

def install(request):
    if os.geteuid()!=0 or not Path('/etc/uranos-live').is_file() or not Path('/run/archiso/bootmnt').is_mount():
        raise RuntimeError('Установка разрешена только из Uranos Live ISO.')
    if os.getpid()!=1:
        raise RuntimeError('Запускай установку через штатный изолированный помощник.')
    os.chdir('/')
    lock=open('/run/uranos-install.lock','w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    r=validate_request(request)
    password=request.pop('password')
    wallpaper=None
    if r.wallpaper:
        # Decode and re-encode before partitioning; never copy arbitrary host files as root.
        from PySide6.QtGui import QImage
        image=QImage(r.wallpaper)
        if image.isNull() or image.width()*image.height()>40000000:
            raise ValueError('Не удалось прочитать изображение обоев (до 40 Мп).')
        wallpaper=tempfile.NamedTemporaryFile(suffix='.png')
        if not image.save(wallpaper.name): raise ValueError('Не удалось подготовить обои.')
    packages=['base','linux','linux-firmware','intel-ucode','amd-ucode','grub','efibootmgr','sudo',
              'networkmanager','sddm','pipewire','pipewire-pulse','wireplumber','btrfs-progs','dosfstools',
              'plasma-nm','xdg-desktop-portal-kde','polkit-kde-agent','python']+DESKTOPS[r.desktop]+r.packages
    if r.snapshots: packages+=['snapper']
    packages=list(dict.fromkeys(packages))
    # Resolve all package names before changing the target. Signature verification remains enabled.
    run('pacman','-Sy','--noconfirm')
    run('pacman','-Sp','--print-format','%n',*packages, capture=True)
    if request['disk'] not in disks(): raise RuntimeError('Диск стал занят до начала установки.')
    dev=request['disk']['path']; mode=request['boot_mode']
    prefix=dev+('p' if dev[-1].isdigit() else '')
    rootdev=prefix+'2'; bootdev=prefix+'1'
    run('wipefs','--all',dev)
    run('sgdisk','--zap-all',dev)
    if mode=='uefi':
        run('sgdisk','-n','1:0:+1G','-t','1:ef00','-n','2:0:0','-t','2:8300',dev)
    else:
        run('sgdisk','-n','1:0:+2M','-t','1:ef02','-n','2:0:0','-t','2:8300',dev)
    run('partprobe',dev); run('udevadm','settle')
    target=Path(tempfile.mkdtemp(prefix='uranos-target-',dir='/mnt'))
    mounted=False
    try:
        if r.filesystem=='btrfs':
            run('mkfs.btrfs','-f','-L','Uranos',rootdev)
            run('mount',rootdev,target); mounted=True
            for sub in ('@','@home','@log','@cache'):
                run('btrfs','subvolume','create',target/sub)
            run('umount',target); mounted=False
            run('mount','-o','subvol=@,compress=zstd',rootdev,target); mounted=True
            for sub,path in (('@home','home'),('@log','var/log'),('@cache','var/cache')):
                (target/path).mkdir(parents=True,exist_ok=True)
                run('mount','-o',f'subvol={sub},compress=zstd',rootdev,target/path)
        else:
            run('mkfs.ext4','-F','-L','Uranos',rootdev)
            run('mount',rootdev,target); mounted=True
        if mode=='uefi':
            run('mkfs.fat','-F','32',bootdev)
            (target/'efi').mkdir(); run('mount',bootdev,target/'efi')
        run('pacstrap','-K',target,*packages)
        write(target,'etc/fstab',run('genfstab','-U',target,capture=True))
        def ch(*args,**kw): return run('arch-chroot',target,*args,**kw)
        apply_branding(target)
        write(target,'etc/hostname','uranos\n')
        write(target,'etc/locale.gen','en_US.UTF-8 UTF-8\nru_RU.UTF-8 UTF-8\n')
        write(target,'etc/locale.conf','LANG=ru_RU.UTF-8\n')
        write(target,'etc/vconsole.conf','KEYMAP=us\nFONT=cyr-sun16\n')
        ch('locale-gen'); ch('ln','-sf','/usr/share/zoneinfo/UTC','/etc/localtime')
        ch('useradd','-m','-G','wheel','-s','/bin/bash','-c',r.full_name,r.username)
        ch('chpasswd',input=r.username+':'+password+'\n'); password=None
        ch('passwd','-l','root')
        write(target,'etc/sudoers.d/10-uranos','%wheel ALL=(ALL:ALL) ALL\n',0o440)
        ch('visudo','-cf','/etc/sudoers')
        home='home/'+r.username
        write(target,home+'/.config/kxkbrc','[Layout]\nUse=true\nLayoutList='+r.keyboard+'\nOptions=grp:alt_shift_toggle\nResetOldOptions=true\n')
        if wallpaper:
            dest=target/home/'.local/share/wallpapers/uranos.png'; dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(wallpaper.name,dest)
            write(target,home+'/.config/autostart/uranos-wallpaper.desktop',
                '[Desktop Entry]\nType=Application\nName=Uranos wallpaper\nExec=/usr/local/bin/uranos-first-login\n')
            write(target,'usr/local/bin/uranos-first-login',
                '#!/bin/bash\nset -e\nfor attempt in {1..30}; do\n'
                ' if plasma-apply-wallpaperimage "$HOME/.local/share/wallpapers/uranos.png"; then\n'
                '  rm -- "$HOME/.config/autostart/uranos-wallpaper.desktop"\n  exit 0\n fi\n sleep 1\ndone\nexit 1\n',0o755)
        write(target,'etc/sddm.conf.d/uranos.conf','[Users]\nRememberLastUser=true\n[General]\nDefaultSession=plasma.desktop\n')
        ch('systemctl','enable','NetworkManager','sddm','systemd-timesyncd')
        ch('systemctl','set-default','graphical.target')
        write(target,'etc/default/grub',f'GRUB_DEFAULT=0\nGRUB_TIMEOUT={r.boot_timeout}\nGRUB_DISTRIBUTOR="Uranos Vanilla"\nGRUB_CMDLINE_LINUX_DEFAULT="quiet"\nGRUB_DISABLE_OS_PROBER=true\n')
        ch('mkinitcpio','-P')
        if mode=='uefi':
            ch('grub-install','--target=x86_64-efi','--efi-directory=/efi','--bootloader-id=Uranos','--removable','--no-nvram')
        else: ch('grub-install','--target=i386-pc',dev)
        ch('grub-mkconfig','-o','/boot/grub/grub.cfg')
        if r.snapshots:
            ch('snapper','--no-dbus','-c','root','create-config','/')
            ch('snapper','--no-dbus','-c','root','set-config','TIMELINE_CREATE=yes','TIMELINE_CLEANUP=yes',
               'TIMELINE_LIMIT_HOURLY='+str(r.snapshot_keep if r.snapshot_schedule=='hourly' else 0),
               'TIMELINE_LIMIT_DAILY='+str(r.snapshot_keep),'TIMELINE_LIMIT_WEEKLY=0',
               'TIMELINE_LIMIT_MONTHLY=0','TIMELINE_LIMIT_YEARLY=0')
            write(target,'etc/systemd/system/snapper-timeline.timer.d/uranos.conf',
                  '[Timer]\nOnCalendar=\nOnCalendar='+r.snapshot_schedule+'\n')
            ch('systemctl','enable','snapper-timeline.timer','snapper-cleanup.timer')
        actual=ch('pacman','-Q',capture=True)
        report={'schema':1,'status':'installed','recipe':asdict(r),'boot_mode':mode,
                'disk':request['disk'],'packages':[dict(zip(('name','version'),line.split(' ',1))) for line in actual.splitlines()],
                'limitations':['Full snapshot rollback and booting snapshots are not implemented.']}
        write(target,home+'/Uran.conf',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        ch('chown','-R',r.username+':'+r.username,'/home/'+r.username)
        if r.snapshots: ch('snapper','--no-dbus','-c','root','create','--description','Uranos initial installation','--cleanup-algorithm','number')
        run('sync')
    finally:
        password=None
        primary_error=sys.exc_info()[1]
        if mounted:
            try: cleanup_target(target)
            except Exception as cleanup_error:
                if primary_error is None: raise
                print('Дополнительно при очистке: '+str(cleanup_error),file=sys.stderr,flush=True)
        if target.exists() and not os.path.ismount(target): target.rmdir()
    print('URANOS_INSTALL_SUCCESS',flush=True)

if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.readline(2*1024*1024))
        install(request)
    except Exception as exc:
        print('Установка остановлена: '+str(exc),file=sys.stderr,flush=True)
        sys.exit(1)
