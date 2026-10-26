/* Keep upload bytes independent of the browser's native file-input lifetime. */
(function(root){
  async function snapshotFile(source){
    const name=source?.name||'未命名材料';
    let bytes;
    try{bytes=await source.arrayBuffer();}catch(error){throw new Error('无法读取“'+name+'”，请重新选择这份材料。');}
    if(!bytes.byteLength||bytes.byteLength!==source.size)throw new Error('“'+name+'”的文件内容为空或不完整，请重新选择这份材料；已有结果仍保留。');
    return new File([bytes],name,{type:source.type,lastModified:source.lastModified||Date.now()});
  }
  async function captureFiles(files){return Promise.all(Array.from(files,snapshotFile));}
  function restoreItemFile(item){
    const bytes=item.fileBytes;
    if(bytes instanceof ArrayBuffer&&bytes.byteLength>0){
      if(item.file?.size&&bytes.byteLength!==item.file.size)throw new Error('“'+(item.file?.name||item.file_name)+'”的已保存内容不完整，请重新选择这份材料。');
      return new File([bytes],item.file?.name||item.file_name||'未命名材料',{type:item.file?.type||item.file_type,lastModified:item.file?.lastModified||Date.now()});
    }
    return item.file;
  }
  async function prepareItems(items){
    const copies=await captureFiles(items.map(restoreItemFile));
    const buffers=await Promise.all(copies.map(file=>file.arrayBuffer()));
    copies.forEach((file,index)=>{items[index].file=file;items[index].fileBytes=buffers[index];});
  }
  function classificationFailures(items){
    return items.filter(item=>item.categorySource!=='manual' &&
      (item.classificationError||item.categoryNote?.startsWith('自动分类失败')||item.categorySource==='unclassified'));
  }
  function confirmCategory(item,category,label){
    item.category=category;item.categorySource='manual';item.classificationError=null;
    item.categoryNote='已人工确认：'+label;item.status='pending';
    item.results=[];item.ocrResults=[];item.visionResults=[];item.feedback={};item.error='';
  }
  async function requestClassification(item,{fetchImpl=root.fetch,timeoutMs=120000,allowedCategories=[]}={}){
    // A restored browser File must be readable before starting a multipart request.
    await prepareItems([item]);const file=item.file;
    for(let attempt=0;attempt<2;attempt++){
      const controller=new AbortController();
      const timer=setTimeout(()=>controller.abort(),timeoutMs);
      try{
        const form=new FormData();form.append('file',file,file.name);
        let response;
        try{response=await fetchImpl('/api/v1/vision/lab/classify',{method:'POST',body:form,signal:controller.signal});}
        catch(error){
          if(controller.signal.aborted)throw new Error('分类等待超时，请重试分类或人工确认材料类别。');
          if(error instanceof TypeError){
            if(attempt===0)continue;
            throw new Error('分类请求连接中断，自动重试仍未成功。请重试失败分类，或人工确认材料类别后继续。');
          }
          throw error;
        }
        let data;
        try{data=await response.json();}catch(error){
          if(controller.signal.aborted)throw new Error('分类等待超时，请重试分类或人工确认材料类别。');
          throw new Error('分类接口未返回有效结果（HTTP '+response.status+'），请重试或人工确认类别。');
        }
        if(!response.ok)throw new Error('分类服务 HTTP '+response.status+'：'+(typeof data.detail==='string'?data.detail:'请求失败，请重试或人工确认类别。'));
        if(!data||typeof data.category!=='string'||(allowedCategories.length&&!allowedCategories.includes(data.category)))throw new Error('分类结果的材料类别无效，请人工确认类别。');
        return data;
      }finally{clearTimeout(timer);}
    }
  }
  const api={snapshotFile,captureFiles,restoreItemFile,prepareItems,classificationFailures,confirmCategory,requestClassification};root.PvMaterialFiles=api;
  if(typeof module==='object'&&module.exports)module.exports=api;
})(globalThis);
