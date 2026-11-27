"""Uranos identity for both the live image and installed systems."""
from pathlib import Path
import os

OS_RELEASE = '\n'.join([
    'NAME="Uranos Linux"', 'PRETTY_NAME="Uranos Linux"', 'ID=uranos',
    'ID_LIKE=arch', 'BUILD_ID=rolling', 'VARIANT="Vanilla"', 'VARIANT_ID=vanilla',
    'ANSI_COLOR="38;2;20;91;104"', 'LOGO=uranos-logo', '',
])
LSB_RELEASE = 'DISTRIB_ID=Uranos\nDISTRIB_RELEASE=rolling\nDISTRIB_DESCRIPTION="Uranos Linux"\n'
ISSUE = r'Uranos Linux \r (\l)' + '\n\n'
LOGO = '''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128"><rect width="128" height="128" rx="28" fill="#145b68"/><path d="M36 30v43a28 28 0 0 0 56 0V30" fill="none" stroke="#eefbff" stroke-width="12" stroke-linecap="round"/><ellipse cx="64" cy="67" rx="53" ry="18" transform="rotate(-25 64 67)" fill="none" stroke="#6bd5e1" stroke-width="5"/></svg>'''


def _write(root, path, content, mode=0o644):
    path = Path(root) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.uranos-' + path.name)
    temporary.write_text(content, encoding='utf-8')
    temporary.chmod(mode)
    os.replace(temporary, path)


def apply_branding(root):
    # Replace paths atomically: /etc/os-release may be a symlink into /usr.
    for path in ('etc/os-release', 'usr/lib/os-release'):
        _write(root, path, OS_RELEASE)
    _write(root, 'etc/lsb-release', LSB_RELEASE)
    _write(root, 'etc/issue', ISSUE)
    _write(root, 'usr/share/icons/hicolor/scalable/apps/uranos-logo.svg', LOGO + '\n')
    # Reapply before mkinitcpio hooks when upstream identity files are upgraded.
    helper = '#!/bin/sh\nset -eu\n'
    for path, content in (('/etc/os-release', OS_RELEASE), ('/usr/lib/os-release', OS_RELEASE),
                          ('/etc/lsb-release', LSB_RELEASE), ('/etc/issue', ISSUE)):
        helper += "cat >" + path + ".uranos-new <<'URANOS_IDENTITY'\n" + content + "URANOS_IDENTITY\n"
        helper += 'chmod 644 ' + path + '.uranos-new\nmv -f ' + path + '.uranos-new ' + path + '\n'
    _write(root, 'usr/local/libexec/uranos-branding', helper, 0o755)
    _write(root, 'etc/pacman.d/hooks/10-uranos-branding.hook',
           '[Trigger]\nOperation = Install\nOperation = Upgrade\nType = Package\nTarget = filesystem\nTarget = lsb-release\n\n'
           '[Action]\nDescription = Applying Uranos Linux identity...\nWhen = PostTransaction\n'
           'Exec = /usr/local/libexec/uranos-branding\n')


if __name__ == '__main__':
    import sys
    apply_branding(sys.argv[1])
