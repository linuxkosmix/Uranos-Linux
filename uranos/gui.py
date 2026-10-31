import sys
import json
import os
from pathlib import Path
from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import (QApplication, QWizard, QWizardPage, QVBoxLayout, QFormLayout,
    QLabel, QLineEdit, QCheckBox, QComboBox, QSpinBox, QPlainTextEdit,
    QPushButton, QFileDialog, QMessageBox, QTreeWidget, QTreeWidgetItem, QInputDialog)
from .installer import disks
from .model import Recipe, CATALOG, DESKTOPS

class Wizard(QWizard):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Uranos Vanilla — подготовка системы")
        self.resize(960, 700)
        self.live = Path("/etc/uranos-live").exists()
        self.running = False
        self.selected = set()
        self.configs = {}
        self.active_desktop = None
        self.setButtonText(QWizard.NextButton, "Далее")
        self.setButtonText(QWizard.BackButton, "Назад")
        self.setButtonText(QWizard.FinishButton, "Установить" if self.live else "Сохранить рецепт")
        self.setButtonText(QWizard.CancelButton, "Закрыть")
        intro = self.page_new("Uranos Vanilla", "Система твоя уже после первого ввода пароля.")
        intro.layout().addWidget(QLabel("Установка через интернет на целый диск. Все данные выбранного диска будут удалены.\nВ текущей версии устанавливается KDE Plasma; ручной разметки пока нет."))
        page = self.page_new("Пользователь и клавиатура")
        form = QFormLayout(); page.layout().addLayout(form)
        self.username = QLineEdit(); self.name = QLineEdit()
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.Password)
        self.admin = QCheckBox("Права администратора"); self.admin.setChecked(True)
        self.keyboard = QComboBox(); self.keyboard.addItems(["us,ru", "us"])
        for label, widget in [("Логин",self.username),("Имя",self.name),("Пароль",self.password),("Повтор пароля",self.confirm),("",self.admin),("Раскладки",self.keyboard),("Проверка ввода",QLineEdit())]: form.addRow(label,widget)
        page = self.page_new("Файловая система и снапшоты", "Выбранный диск будет полностью стёрт только после итогового подтверждения.")
        form = QFormLayout(); page.layout().addLayout(form)
        self.disk = QComboBox()
        refresh = QPushButton("Обновить список дисков"); refresh.clicked.connect(self.refresh_disks)
        form.addRow("Диск для установки", self.disk); form.addRow("", refresh)
        self.boot_mode=QComboBox(); self.boot_mode.addItems(["uefi", "bios"])
        self.boot_mode.setCurrentText("uefi" if Path('/sys/firmware/efi').exists() else "bios")
        form.addRow("Режим загрузки", self.boot_mode)
        self.refresh_disks()
        self.fs = QComboBox(); self.fs.addItems(["btrfs", "ext4"])
        self.snapshots = QCheckBox("Автоматические снимки системы"); self.snapshots.setChecked(True)
        self.keep = QSpinBox(); self.keep.setRange(1,100); self.keep.setValue(7)
        self.schedule = QComboBox(); self.schedule.addItems(["daily", "hourly"])
        self.timeout = QSpinBox(); self.timeout.setRange(0,60); self.timeout.setValue(5)
        for label,widget in [("Файловая система",self.fs),("",self.snapshots),("Расписание",self.schedule),("Хранить снимков",self.keep),("Таймаут GRUB (сек.)",self.timeout)]: form.addRow(label,widget)
        page.layout().addWidget(QLabel("Снимки пользовательских данных и загрузка из снимка пока не реализованы."))
        self.fs.currentTextChanged.connect(self.fs_changed)
        self.snapshots.toggled.connect(self.snapshot_changed)
        page = self.page_new("Рабочая среда")
        self.desktop = QComboBox(); self.desktop.addItems(list(DESKTOPS))
        for name in ["i3", "Xfce", "Cinnamon", "MATE", "Sway"]:
            self.desktop.addItem(name + " — в разработке")
            self.desktop.model().item(self.desktop.count()-1).setEnabled(False)
        if self.live:
            for n in (1,2):
                self.desktop.setItemText(n, self.desktop.itemText(n)+" — установка в разработке")
                self.desktop.model().item(n).setEnabled(False)
        page.layout().addWidget(self.desktop)
        page = self.page_new("Глубокая настройка рабочей среды")
        self.desktop_page_id = self.pageIds()[-1]
        self.addon_note = QLabel(); self.addon_note.setWordWrap(True); page.layout().addWidget(self.addon_note)
        self.editor = QPlainTextEdit(); page.layout().addWidget(self.editor)
        self.editor.setPlaceholderText("Дополнительные настройки. Версионный шаблон появится после проверки пакетов выбранного выпуска.")
        page = self.page_new("Программы")
        self.view = QComboBox(); self.view.addItems(["Категории", "Список"])
        self.search = QLineEdit(); self.search.setPlaceholderText("Поиск по названию пакета")
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Программа", "Пакет"])
        for widget in [self.view,self.search,self.tree]: page.layout().addWidget(widget)
        self.view.currentIndexChanged.connect(self.render_packages)
        self.search.textChanged.connect(self.render_packages)
        self.tree.itemChanged.connect(self.package_changed)
        self.render_packages()
        page = self.page_new("Обои")
        self.wallpaper = QLineEdit(); self.wallpaper.setReadOnly(True)
        button = QPushButton("Выбрать изображение"); button.clicked.connect(self.choose_wallpaper)
        page.layout().addWidget(self.wallpaper); page.layout().addWidget(button)
        page = self.page_new("Проверь настройки", "Рецепт не содержит пароль и не является отчётом об установленной системе.")
        self.summary_id = self.pageIds()[-1]
        self.summary = QPlainTextEdit(); self.summary.setReadOnly(True); page.layout().addWidget(self.summary)
        self.currentIdChanged.connect(self.page_changed)
        self.process=QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.finished.connect(self.install_finished)
        self.process.errorOccurred.connect(self.process_error)


    def refresh_disks(self):
        self.disk.clear()
        try:
            for d in disks():
                self.disk.addItem(f"{d['path']} — {d['model']} — {d['size']/1024**3:.1f} ГБ", d)
        except Exception:
            pass
        if not self.disk.count(): self.disk.addItem("Нет свободных дисков (от 16 ГБ)", None)

    def reject(self):
        if self.running:
            QMessageBox.warning(self,"Идёт установка","Дождись завершения установки."); return
        super().reject()

    def closeEvent(self,event):
        if self.running: event.ignore()
        else: event.accept()

    def read_progress(self):
        self.summary.appendPlainText(bytes(self.process.readAllStandardOutput()).decode(errors='replace'))

    def process_error(self,error):
        if error == QProcess.FailedToStart:
            self.install_finished(-1, QProcess.CrashExit)

    def install_finished(self,code,status):
        self.running=False
        self.button(QWizard.CancelButton).setEnabled(True)
        self.button(QWizard.BackButton).setEnabled(True)
        self.button(QWizard.FinishButton).setEnabled(True)
        if code==0 and status==QProcess.NormalExit:
            self.button(QWizard.FinishButton).setEnabled(False)
            self.button(QWizard.BackButton).setEnabled(False)
            QMessageBox.information(self,"Установка завершена","Выключи компьютер, извлеки ISO/флешку и загрузи установленную систему.")
        else:
            QMessageBox.critical(self,"Установка остановлена","Система могла установиться частично. Подробности в журнале на этом экране. Ничего не откатывалось автоматически.")

    def page_new(self,title,subtitle=""):
        page=QWizardPage(); page.setTitle(title); page.setSubTitle(subtitle); page.setLayout(QVBoxLayout()); self.addPage(page); return page

    def fs_changed(self,value):
        enabled=value=="btrfs"; self.snapshots.setEnabled(enabled)
        if not enabled: self.snapshots.setChecked(False)
        self.snapshot_changed(self.snapshots.isChecked())

    def snapshot_changed(self,enabled):
        self.keep.setEnabled(enabled); self.schedule.setEnabled(enabled)

    def package_changed(self,item,column):
        pkg=item.data(0,Qt.UserRole)
        if pkg:
            if item.checkState(0)==Qt.Checked: self.selected.add(pkg)
            else: self.selected.discard(pkg)

    def render_packages(self,*args):
        self.tree.blockSignals(True); self.tree.clear()
        query=self.search.text().lower()
        for category,packages in CATALOG.items():
            parent=QTreeWidgetItem(self.tree,[category]) if self.view.currentIndex()==0 else self.tree
            for pkg,label in packages.items():
                if query not in (pkg+label).lower(): continue
                item=QTreeWidgetItem(parent,[label,pkg]); item.setData(0,Qt.UserRole,pkg)
                item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
                item.setCheckState(0,Qt.Checked if pkg in self.selected else Qt.Unchecked)
        self.tree.expandAll(); self.tree.blockSignals(False)

    def save_editor(self):
        if self.active_desktop: self.configs[self.active_desktop]=self.editor.toPlainText()

    def page_changed(self,index):
        if index==self.desktop_page_id:
            self.save_editor(); self.active_desktop=self.desktop.currentText()
            notes={"KDE Plasma":"Скрипты KWin: каталог совместимых дополнений ещё готовится.","GNOME":"Расширения GNOME: каталог совместимых дополнений ещё готовится.","Hyprland":"Редактор будущей конфигурации Hyprland. Пока это черновик без проверки синтаксиса и применения."}
            self.addon_note.setText(notes[self.active_desktop]); self.editor.setPlainText(self.configs.get(self.active_desktop,""))
        if index==self.summary_id:
            try: self.summary.setPlainText(self.recipe().dumps())
            except ValueError as e: self.summary.setPlainText(str(e))

    def recipe(self):
        self.save_editor()
        return Recipe(username=self.username.text(),full_name=self.name.text(),admin=self.admin.isChecked(),keyboard=self.keyboard.currentText(),filesystem=self.fs.currentText(),snapshots=self.snapshots.isChecked(),snapshot_schedule=self.schedule.currentText(),snapshot_keep=self.keep.value(),boot_timeout=self.timeout.value(),desktop=self.desktop.currentText(),packages=sorted(self.selected),wallpaper=self.wallpaper.text(),desktop_config=self.configs.get(self.desktop.currentText(),""))

    def choose_wallpaper(self):
        path,_=QFileDialog.getOpenFileName(self,"Обои","","Изображения (*.png *.jpg *.jpeg *.webp)")
        if path: self.wallpaper.setText(path)

    def accept(self):
        try:
            if self.password.text()!=self.confirm.text(): raise ValueError("Пароли не совпадают.")
            recipe=self.recipe()
            data=recipe.dumps()
            if self.live:
                disk=self.disk.currentData()
                if not disk: raise ValueError("Выбери диск для установки.")
                if len(self.password.text())<8: raise ValueError("Пароль должен содержать минимум 8 символов.")
                if not recipe.admin: raise ValueError("Первый пользователь должен быть администратором.")
                if recipe.desktop_config.strip(): raise ValueError("Произвольные настройки DE ещё не поддерживаются.")
                text,ok=QInputDialog.getText(self,"Подтвердить удаление данных",
                    f"Все данные на {self.disk.currentText()} будут удалены.\nДля подтверждения введи: ERASE {disk['path']}")
                if not ok: return
                if text != 'ERASE '+disk['path']: raise ValueError("Подтверждение не совпало.")
                request={'recipe':json.loads(data),'disk':disk,'boot_mode':self.boot_mode.currentText(),
                         'confirm':text,'password':self.password.text()}
                payload=(json.dumps(request)+'\n').encode()
                self.password.clear(); self.confirm.clear()
                self.running=True
                for b in (QWizard.BackButton,QWizard.FinishButton,QWizard.CancelButton): self.button(b).setEnabled(False)
                self.summary.setPlainText("Начинается установка…")
                self.process.start('sudo',['-n','/usr/local/libexec/uranos-install'])
                if not self.process.waitForStarted(5000):
                    self.install_finished(-1,QProcess.CrashExit); return
                self.process.write(payload); self.process.closeWriteChannel()
                return
            path,_=QFileDialog.getSaveFileName(self,"Сохранить рецепт","Uranos.recipe.json","JSON (*.json)")
            if not path: return
            Path(path).write_text(data,encoding="utf-8")
            self.password.clear(); self.confirm.clear()
            QMessageBox.information(self,"Рецепт сохранён","Настройки сохранены. Установка системы ещё не выполнялась.")
        except (ValueError,OSError) as e: QMessageBox.critical(self,"Ошибка",str(e))

if __name__=="__main__":
    app=QApplication(sys.argv); app.setStyle("Fusion"); wizard=Wizard(); wizard.show(); sys.exit(app.exec())
