const $ = id => document.getElementById(id);
let jobs = [], results = new Map(), running = false, stopped = false, importing = false;
let message = '等待导入';
function ids() {return jobs.filter(j=>results.get(j.id)==='match').map(j=>j.id);}
function render() {
  const failed = [...results.values()].filter(v=>v==='error').length;
  $('status').textContent = `${message} · 已处理 ${results.size}/${jobs.length} · 匹配 ${ids().length}${failed ? ` · ${failed} 项失败未匹配，可继续重试` : ''}`;
  $('progress').max=jobs.length||1; $('progress').value=results.size;
  $('ids').value=ids().join(', ');
  $('file').disabled=running||importing; $('key').disabled=$('concurrency').disabled=running;
  $('start').disabled=running||importing||!jobs.some(j=>!results.has(j.id)||results.get(j.id)==='error');
  $('start').textContent=results.size ? '继续 / 重试' : '开始筛选';
  $('stop').disabled=!running||stopped;
  $('copy').disabled=$('download').disabled=!ids().length;
}
$('file').onchange=async()=>{
  const file=$('file').files[0];if(!file)return;
  importing=true;jobs=[];results.clear();message='正在导入';render();
  try {
    const body=new FormData();body.append('file',file);
    const response=await fetch('/api/v1/ww-screening/import',{method:'POST',body});
    const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'文件无法读取');
    jobs=data.jobs;$('file-status').textContent=`${file.name} · ${jobs.length} 个岗位`;message='导入完成';
  } catch(e) {message=e.message;} finally {importing=false;render();}
};
$('start').onclick=async()=>{
  const key=$('key').value.trim(), n=Number($('concurrency').value);
  if(!key||!Number.isInteger(n)||n<1||n>15){message='请输入 Key 和 1–15 的整数并发数';render();return;}
  const queue=jobs.filter(j=>!results.has(j.id)||results.get(j.id)==='error');
  running=true;stopped=false;message='正在筛选';render();
  let position=0, errors=0;
  async function worker(){
    while(!stopped&&position<queue.length){
      const job=queue[position++];let decision='error';
      try {
        const response=await fetch('/api/v1/ww-screening/classify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job,api_key:key,model:'deepseek-v4-flash'})});
        if(response.ok){const data=await response.json();if(data.id===job.id&&['other','match','review','error'].includes(data.decision))decision=data.decision;}
      } catch { /* Request failures are never matches. */ }
      results.set(job.id,decision);errors=decision==='error'?errors+1:0;
      if(errors>=5){stopped=true;message='连续失败，已暂停。请检查 Key、余额和网络';}
      render();
    }
  }
  try {await Promise.all(Array.from({length:Math.min(n,queue.length)},worker));}
  finally {running=false;if(!stopped)message='本轮完成';render();}
};
$('stop').onclick=()=>{stopped=true;message='已暂停，等待当前请求结束';render();};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('ids').value);message='已复制';}catch{message='请在文本框中手动复制';}render();};
$('download').onclick=()=>{const url=URL.createObjectURL(new Blob([ids().join('\n')],{type:'text/plain'}));const a=document.createElement('a');a.href=url;a.download='matched-job-ids.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);};
window.addEventListener('beforeunload',e=>{if(running||results.size){e.preventDefault();e.returnValue='';}});
render();
