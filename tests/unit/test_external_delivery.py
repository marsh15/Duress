import hashlib
import hmac
import httpx
import pytest
from duress.external.manifest import Scenario
from duress.limits import DEFAULT_LIMITS
from duress.runner import run_schedule


@pytest.mark.asyncio
async def test_generic_dispatcher_preserves_raw_bytes_and_concurrent_groups():
    raw = b'{ "booking_id": "b1", "amount": 1200 }\n'
    seen = []
    async def receive(request):
        seen.append((request.method, request.url.path, request.content, request.headers['X-Signature']))
        return httpx.Response(200, json={'status':'duplicate'})
    scenario = Scenario(id='concurrent',deliveries=[
        {'id':name,'fixture':'raw','event_id':'event'} for name in ('a','b')],
        steps=[{'deliveries':['a','b']}])
    def builder(delivery, attempt):
        return ('PUT','http://owned.test/hooks',raw,
                {'X-Signature':hmac.new(b'runtime',raw,hashlib.sha256).hexdigest()})
    result = await run_schedule(scenario,base_url='http://owned.test',secret='runtime',roles={},
        limits=DEFAULT_LIMITS,request_builder=builder,transport=httpx.MockTransport(receive))
    assert result.error is None
    assert len(result.attempts) == len(seen) == 2
    assert all(item[:3] == ('PUT','/hooks',raw) for item in seen)
    assert all(item[3] == hmac.new(b'runtime',raw,hashlib.sha256).hexdigest() for item in seen)
