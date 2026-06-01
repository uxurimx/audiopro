.PHONY: install run cinema clean install-system-deps setup

# ── Instalación ────────────────────────────────────────────────────────────────

install-system-deps:
	sudo dnf install -y \
		python3-gobject \
		gstreamer1-plugins-good \
		gstreamer1-plugins-bad-free \
		gstreamer1-plugins-ugly \
		gstreamer1-libav \
		pipewire-jack-audio-connection-kit \
		ffmpeg

install: install-system-deps
	pip install -e ".[dev]"

setup: install
	@test -f .env || (cp .env.template .env && echo ">>> Creado .env — agrega tu OPENAI_API_KEY")

# ── Ejecución ──────────────────────────────────────────────────────────────────

run:
	python -m audifonospro.main

cinema:
	python -m audifonospro.main --mode cinema

translate:
	python -m audifonospro.main --mode translate

# ── Binarios externos ──────────────────────────────────────────────────────────
# Ejecuta solo si vas a usar STT/TTS local

install-whisper:
	@echo "==> Clonando whisper.cpp..."
	mkdir -p ~/tools
	git clone https://github.com/ggerganov/whisper.cpp ~/tools/whisper.cpp 2>/dev/null || \
		git -C ~/tools/whisper.cpp pull
	@echo "==> Compilando (AVX2 activado automáticamente en tu i7)..."
	$(MAKE) -C ~/tools/whisper.cpp -j$$(nproc)
	@echo "==> Descargando modelo small (~150MB)..."
	bash ~/tools/whisper.cpp/models/download-ggml-model.sh small
	@echo "==> whisper.cpp listo: ~/tools/whisper.cpp/build/bin/whisper-cli"

install-piper:
	@echo "==> Instalando piper TTS..."
	mkdir -p ~/tools/piper/models
	wget -qO /tmp/piper.tar.gz \
		https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz
	tar -xzf /tmp/piper.tar.gz -C ~/tools/piper
	@echo "#!/bin/bash" > ~/tools/piper/piper.sh
	@echo 'REAL="$$(readlink -f "$${BASH_SOURCE[0]}")"' >> ~/tools/piper/piper.sh
	@echo 'PIPER_DIR="$$(dirname "$$REAL")/piper"' >> ~/tools/piper/piper.sh
	@echo 'exec env LD_LIBRARY_PATH="$$PIPER_DIR:$$LD_LIBRARY_PATH" "$$PIPER_DIR/piper" "$$@"' >> ~/tools/piper/piper.sh
	chmod +x ~/tools/piper/piper.sh
	ln -sf ~/tools/piper/piper.sh ~/.local/bin/piper
	@echo "==> piper listo: ~/tools/piper/piper.sh"
	@echo "==> Descarga una voz desde: https://huggingface.co/rhasspy/piper-voices"

# ── Limpieza ───────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache dist build *.egg-info

test:
	pytest tests/ -v
