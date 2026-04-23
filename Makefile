VENV_DIR := /opt/rbvoice
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip

# ---- VOSK ----
MODEL_DIR := $(VENV_DIR)/vosk-models
VOICELANG ?= it
MODEL ?= vosk-model-$(VOICELANG)-0.22
MODEL_ZIP := $(MODEL).zip
MODEL_URL := https://alphacephei.com/vosk/models/$(MODEL_ZIP)

# ---- Piper ----
PIPER_DIR := $(VENV_DIR)/piper-models
PIPER_VOICE ?= it_IT-paola-medium
PIPER_BASE_URL := https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium
PIPER_ONNX := $(PIPER_VOICE).onnx
PIPER_JSON := $(PIPER_VOICE).onnx.json
all:
	echo Makefile options

install:
	# binaries
	mkdir -p $(VENV_DIR)/bin
	cp -f *.sh $(VENV_DIR)/bin/
	cp -f *.py $(VENV_DIR)/bin/
	cp -f *.json /boot/firmware/rb
	chmod 755 $(VENV_DIR)/bin/*.sh
	cp rbvoice.service /lib/systemd/system
	systemctl daemon-reload
	systemctl enable rbvoice.service

piper-model:
	mkdir -p $(PIPER_DIR)
	wget -O $(PIPER_DIR)/$(PIPER_ONNX) $(PIPER_BASE_URL)/$(PIPER_ONNX)?download=true
	wget -O $(PIPER_DIR)/$(PIPER_JSON) $(PIPER_BASE_URL)/$(PIPER_JSON)?download=true

vosk-model:
	mkdir -p $(MODEL_DIR)
	cd $(MODEL_DIR) && \
	wget -O $(MODEL_ZIP) $(MODEL_URL) && \
	unzip -o $(MODEL_ZIP) && \
	rm -f $(MODEL_ZIP)
venv:
	mkdir -p $(VENV_DIR)
	chown pi $(VENV_DIR)
	python3 -m venv $(VENV_DIR)
	$(PYTHON) -m pip install --upgrade pip
	$(PIP) install text2num
	$(PIP) install vosk
	$(PIP) install piper-tts

install-dependencies-standalone:
	apt -y install sox

install-dependencies-bluetooth:
	apt -y install bluetoothalsa

install-dependencies-android:
	apt -y install pipewire

install-asoundrc:
	cp asoundrc $HOME/.asoundrc