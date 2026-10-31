from dataclasses import dataclass, field, asdict
import json
import re

CATALOG = {
    "Кодинг": {"git": "Git", "neovim": "Neovim", "python": "Python", "base-devel": "Инструменты сборки"},
    "Игры": {"lutris": "Lutris", "gamemode": "GameMode"},
    "Интернет": {"firefox": "Firefox", "thunderbird": "Thunderbird"},
    "Офис": {"libreoffice-fresh": "LibreOffice"},
    "Мультимедиа": {"vlc": "VLC", "gimp": "GIMP"},
}
DESKTOPS = {"KDE Plasma": ["plasma-meta", "konsole", "dolphin"],
            "GNOME": ["gnome-shell", "gnome-control-center", "gnome-terminal", "nautilus"],
            "Hyprland": ["hyprland", "kitty", "waybar", "mako", "wofi", "hyprpaper", "hyprlock", "xdg-desktop-portal-hyprland"]}

@dataclass
class Recipe:
    schema: int = 1
    release: str = "Uranos Vanilla 0.1-dev"
    username: str = ""
    full_name: str = ""
    admin: bool = True
    keyboard: str = "us,ru"
    filesystem: str = "btrfs"
    snapshots: bool = True
    snapshot_schedule: str = "daily"
    snapshot_keep: int = 7
    bootloader: str = "grub"
    boot_timeout: int = 5
    desktop: str = "KDE Plasma"
    packages: list[str] = field(default_factory=list)
    wallpaper: str = ""
    desktop_config: str = ""

    def validate(self):
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", self.username):
            raise ValueError("Имя пользователя: латинские строчные буквы, цифры, _ и -; первая буква или _.")
        if self.username in ('root', 'nobody', 'daemon', 'bin', 'systemd-network', 'sddm'):
            raise ValueError("Это имя зарезервировано системой.")
        if any(ord(c) < 32 for c in self.full_name) or ':' in self.full_name or len(self.full_name)>100:
            raise ValueError("Недопустимое отображаемое имя.")
        if self.keyboard not in ('us', 'us,ru') or self.bootloader != 'grub':
            raise ValueError("Неподдерживаемая раскладка или загрузчик.")
        if type(self.boot_timeout) is not int or not 0 <= self.boot_timeout <= 60:
            raise ValueError("Некорректный таймаут загрузчика.")
        if self.snapshot_schedule not in ('daily', 'hourly'):
            raise ValueError("Некорректное расписание снимков.")
        if self.desktop not in DESKTOPS:
            raise ValueError("Рабочая среда пока не поддерживается.")
        if self.filesystem not in ("btrfs", "ext4"):
            raise ValueError("Неизвестная файловая система.")
        if self.snapshots and self.filesystem != "btrfs":
            raise ValueError("Снапшоты доступны только для Btrfs.")
        known = {p for category in CATALOG.values() for p in category}
        if set(self.packages) - known:
            raise ValueError("Пакет отсутствует в каталоге.")
        if not 1 <= self.snapshot_keep <= 100:
            raise ValueError("Некорректное число снимков.")

    def dumps(self):
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"
