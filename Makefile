.PHONY: install uninstall check
install:
	./install.sh
uninstall:
	./uninstall.sh
check:
	python3 -m py_compile mek/*.py jarvis.py && bash -n install.sh uninstall.sh bin/*.sh && echo OK
