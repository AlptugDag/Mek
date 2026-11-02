.PHONY: install uninstall check test
install:
	./install.sh
uninstall:
	./uninstall.sh
check:
	python3 -m py_compile jarvisd/*.py jarvis.py && bash -n install.sh uninstall.sh bin/jarvis jarvisd/install_privileged.sh && echo OK
test:
	python3 -m unittest discover -s tests -v
