.PHONY: install uninstall check test app

# Testler ve denetimler, kurulumun bağımlılıklarını içeren sanal ortamla
# çalışır; yoksa sistem python3'üne düşer (bu durumda psutil/numpy gerektiren
# testler atlanır).
PY := $(shell [ -x "$$HOME/.jarvis/venv/bin/python" ] && echo "$$HOME/.jarvis/venv/bin/python" || command -v python3)

install:
	./install.sh

uninstall:
	./uninstall.sh

# Yalnızca /Applications/Mek.app'i yeniden derler (App Intents / Siri dahil).
app:
	./bin/build_mek_app.sh

check:
	$(PY) -m py_compile jarvisd/*.py jarvis.py bin/appintents_metadata.py && \
	bash -n install.sh uninstall.sh bin/jarvis bin/build_mek_app.sh jarvisd/install_privileged.sh && \
	echo OK

test:
	$(PY) -m unittest discover -s tests -v
