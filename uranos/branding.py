from pathlib import Path
import os

OS_RELEASE = '\n'.join([
    'NAME="Uranos Linux"', 'PRETTY_NAME="Uranos Linux"', 'ID=uranos',
    'ID_LIKE=arch', 'BUILD_ID=rolling', 'VARIANT="Vanilla"', 'VARIANT_ID=vanilla',
    'ANSI_COLOR="38;2;20;91;104"', 'LOGO=start-here', '',
])

def apply_branding(root):
    """Replace /etc override atomically, without following Arch's os-release symlink."""
    etc=Path(root)/'etc'; etc.mkdir(parents=True,exist_ok=True)
    temp=etc/'.uranos-os-release'
    temp.write_text(OS_RELEASE);temp.chmod(0o644)
    os.replace(temp,etc/'os-release')

if __name__=='__main__':
    import sys
    apply_branding(sys.argv[1])
