# Speech model research

Reviewed 20 October 2026. These are candidates and source claims, not measurements made
with CBS interviews. The offline [speech catalog](model-catalog.md) is implemented;
the [local enhancement runner](local-enhancement.md) includes bounded long-text alignment.
[UCloud enhancement](ucloud-enhancement.md) uses the same pipeline in a prepared SSH job.
Both have mocked acceptance; actual inference and validated speech hosting remain pending.

## UCloud Transcriber compared with OpenAI whisper-1

OpenAI documents `whisper-1` as based on open-source Whisper V2, not large-v3.
It can return word and segment timestamps. Speaker diarization requires another model
or a separate processing stage.
[OpenAI API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)

UCloud currently lists Whisper base, small, medium, large-v3, large-v3-turbo and NVIDIA
Parakeet TDT 0.6B v3. It defaults to large-v3. Its documentation describes WhisperX
speaker diarization and notes reduced Danish accuracy with turbo compared with large-v3.
Its memory table is a starting point; long inputs require more memory.
[UCloud documentation](https://docs.cloud.sdu.dk/Apps/transcriber.html)

| Candidate | Role in a Danish/English evaluation |
| --- | --- |
| Whisper base / small | Low-resource alternatives; evaluate the accuracy cost. |
| Whisper medium | Intermediate size and resource requirements. |
| Whisper large-v3 | Main multilingual quality baseline against API Whisper V2. |
| Whisper large-v3-turbo | Faster multilingual alternative; measure Danish quality separately. |
| Parakeet TDT 0.6B v3 | Different model family; supports Danish/English and word/segment timestamps. |
| Hviske v5.3 | Danish specialist candidate; separate alignment/diarization needed. |
| Distil large-v3.5 | English-only candidate, unsuitable as the Danish default. |

OpenAI's large-v3 card reports 10–20% fewer errors than large-v2 across a range of
languages. This is a relative error reduction, not a guaranteed improvement for a
particular CBS interview. Smaller Whisper models trade accuracy for resources; speed
depends on runtime, hardware, recording length and language.
[Large-v3 model card](https://huggingface.co/openai/whisper-large-v3),
[Whisper documentation](https://github.com/openai/whisper)

Parakeet documents Danish/English support and native word/segment timestamps, but that
does not establish its ranking against whisper-1 on CBS interviews or guarantee the
same fields in every hosted endpoint.
[NVIDIA model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)

Hviske v5.3 is a roughly 2B-parameter Conformer model, not a Whisper fine-tune.
Its card describes Danish transcription, JSON/text responses and no verbose_json.
The model uses custom repository code and CC-BY-NC-4.0 licensing. Its documented
vLLM setup uses a different version from the hosting agent's tested runtime, so it
needs a reviewed and tested speech profile before deployment. Publisher benchmarks
do not replace our independent evaluation.
[Hviske model card](https://huggingface.co/syvai/hviske-v5.3)

Distil large-v3.5 is intended for English.
[Distil model card](https://huggingface.co/distil-whisper/distil-large-v3.5)

## Catalog and optional enhancement direction

Follow the hosting agent's packaged JSON catalog pattern: model ID, pinned revision,
parameter provenance, language, runtime/task/endpoint, licensing notes, candidate or
validated status, and measured allocation/evidence. Leave untested hardware fields
unknown. Selecting a candidate must not start a job or claim endpoint compatibility.

Track transcription, forced alignment and diarization as separate capabilities.
WhisperX combines speech recognition, audio/text alignment and pyannote speaker detection.
For existing text, use only the requested enhancement stages and save a new result.
[WhisperX documentation](https://github.com/m-bain/whisperX)

The local CPU/CUDA runner and separate UCloud SSH executor are implemented. The app coordinates
owned enhancement jobs through the released UCloud API client; the hosting agent remains
responsible for future vLLM serving deployments. Keep these independent of
the existing UCloud Transcriber app integration. Both paths must preserve the original,
report uncertain or missing timing, and support explicit cancellation/retry. Remote
execution must respect Protected project approvals without silently falling back to
an external service.

Evaluate WER/CER, word timing, speaker attribution, time and peak memory on reviewed
Danish and English interviews. The current short FLEURS clips test transcription and
application workflow; they do not establish multi-speaker interview performance.
