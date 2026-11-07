/* Complete-response deadlines: receiving headers does not end the timeout. */
((root) => {
  async function send(path, options, {signal}={}) {
    const controller=new AbortController();
    let timedOut=false;
    const abort=()=>controller.abort();
    if(signal?.aborted)abort();
    else signal?.addEventListener('abort',abort,{once:true});
    const timer=setTimeout(()=>{timedOut=true;abort();},30000);
    try {
      if(controller.signal.aborted)throw new DOMException('Aborted','AbortError');
      const response=await fetch(path,{...options,signal:controller.signal});
      const text=await response.text();
      return {response,text};
    } catch(error) {
      const failure=new Error(error.name==='AbortError'?
        (timedOut?'Request timed out after 30 seconds.':'Request cancelled.'):
        'Could not receive a complete response from the local server.');
      failure.code=error.name==='AbortError'?(timedOut?'timeout':'cancelled'):'network';
      throw failure;
    } finally {
      clearTimeout(timer);signal?.removeEventListener('abort',abort);
    }
  }
  function recoveryMessage(error, method, hasIdempotencyKey=false) {
    const start=error.code==='timeout'?'Request timed out after 30 seconds.':
      error.code==='cancelled'?'Request cancelled.':'Could not receive a complete response from the local server.';
    if(method==='GET'||method==='HEAD')return `${start} Your inputs are preserved. Check the server and try again.`;
    const recovery=method==='POST'&&hasIdempotencyKey?
      'If you retry, keep the same Idempotency-Key and body.':
      'Fetch the current data before deciding whether to retry.';
    return `${start} The write may have completed on the server. ${recovery} No automatic retry was sent.`;
  }
  const api={send,recoveryMessage};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.restTransport=api;
})(typeof window==='undefined'?globalThis:window);
