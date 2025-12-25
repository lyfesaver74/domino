FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app

# System deps (ffmpeg is useful for various audio formats)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

# Ensure cuDNN shared libraries are present for CTranslate2/faster-whisper CUDA mode.
# The base image may include CUDA but not ship cuDNN runtime libs in all cases.
RUN conda install -y -c nvidia "cudnn>=9,<10" \
  && conda clean -a -y

ENV LD_LIBRARY_PATH=/opt/conda/lib:${LD_LIBRARY_PATH}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Model config via env (can override in docker-compose)
ENV WHISPER_MODEL=base.en
ENV WHISPER_DEVICE=cpu
ENV WHISPER_COMPUTE_TYPE=int8

EXPOSE 9000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
