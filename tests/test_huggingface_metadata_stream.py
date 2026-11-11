"""Access checks need status headers, never untrusted account/model response bodies."""

import httpx

from cbs_transcriber import huggingface_access


def test_hf_access_closes_both_status_responses_without_buffering_any_body():
    streams = []

    class NoBody(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            raise AssertionError("An access check must not read the response body")
            yield b""  # pragma: no cover

        def close(self):
            self.closed = True

    def handle(request):
        assert request.method == "GET" and request.url.host == "huggingface.co"
        assert request.headers["authorization"] == "Bearer hf_SYNTHETIC"
        stream = NoBody()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = huggingface_access.check_token("hf_SYNTHETIC", client=client)
    assert result["token_status"] == "valid" and result["speaker_model_access"] == "allowed"
    assert len(streams) == 2 and all(stream.closed for stream in streams)
