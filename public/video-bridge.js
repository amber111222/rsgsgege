class VideoAudioBridge extends EventTarget {
  constructor(localAudio) {
    super();this.local=localAudio;this._src='';this._paused=true;this._duration=NaN;this._volume=.75;this._muted=false;this._token=0;this.mode='video';this.error=null;this.matches=new Map();this.trim={start:0,end:0};this.videoId='';this.activeMatches=[];this.failedVideos=new Set();
    for(const type of ['play','pause','ended','timeupdate','loadedmetadata','error'])localAudio.addEventListener(type,()=>{if(this.mode==='local')this.dispatchEvent(new Event(type));});
    document.querySelector('#video-manual-form').onsubmit=async event=>{event.preventDefault();const input=document.querySelector('#video-url');try{const url=new URL(input.value);const host=url.hostname.toLowerCase();if(!['youtube.com','www.youtube.com','m.youtube.com','youtu.be'].includes(host)||url.protocol!=='https:')throw Error('URL');const id=host==='youtu.be'?url.pathname.slice(1).split('/')[0]:(url.searchParams.get('v')||url.pathname.match(/^\/(?:shorts|embed)\/([\w-]{11})/)?.[1]);if(!/^[\w-]{11}$/.test(id||''))throw Error('ID');const key=new URL(this._src).pathname;try{localStorage.setItem('volna.video.'+key,id);}catch{}this.manualId=id;this.loadedSource='';await this.play();input.value='';}catch{document.querySelector('#video-meta').textContent=this.error?.message||'Вставь ссылку на конкретное видео YouTube.';}};
    for(const edge of ['start','end'])document.querySelector('#trim-'+edge).onclick=()=>this.setBoundary(edge);
    document.querySelector('#trim-reset').onclick=()=>{this.trim={start:0,end:0};this.saveTrim();};
    document.querySelector('#video-auto').onclick=async()=>{try{localStorage.removeItem('volna.video.'+new URL(this._src).pathname);localStorage.removeItem('volna.match.v3.'+new URL(this._src).pathname);}catch{}this.matches.clear();this.failedVideos.clear();this.manualId=null;this.loadedSource='';try{await this.play();}catch{}};
    this.updateTrim();
    setInterval(()=>{if(this.mode==='video'&&this.player?.getCurrentTime&&!this._paused){if(this.trim.end&&this.currentTime>=this.trim.end&&!this.trimEnded){this.trimEnded=true;this.pause();this.dispatchEvent(new Event('ended'));return;}this.dispatchEvent(new Event('timeupdate'));}},200);
    document.addEventListener('visibilitychange',()=>{if(document.hidden&&this.mode==='video')this.pause();});
  }
  set src(value){this._src=value;this.mode=value.startsWith('blob:')?'local':'video';if(this.mode==='local')this.local.src=value;}
  get src(){return this._src;}
  get paused(){return this.mode==='local'?this.local.paused:this._paused;}
  get duration(){return this.mode==='local'?this.local.duration:this.player?.getDuration?.()||this._duration;}
  get currentTime(){return this.mode==='local'?this.local.currentTime:this.player?.getCurrentTime?.()||0;}
  set currentTime(value){if(this.mode==='local')this.local.currentTime=value;else {this.trimEnded=false;this.player?.seekTo?.(Math.max(this.trim.start||0,Math.min(value,this.trim.end||Infinity)),true);}}
  get volume(){return this._volume;}
  set volume(value){this._volume=Math.max(0,Math.min(1,value));this.local.volume=this._volume;this.player?.setVolume?.(this._volume*100);}
  get muted(){return this._muted;}
  set muted(value){this._muted=!!value;this.local.muted=this._muted;if(this._muted)this.player?.mute?.();else this.player?.unMute?.();}
  load(){this._token++;this.controller?.abort();this.loadedSource='';this.videoId='';this.activeMatches=[];this.trim={start:0,end:0};this.trimEnded=false;this.updateTrim();this._paused=true;this.error=null;this.local.pause();this.player?.stopVideo?.();if(this.mode==='local'){this.local.load();document.querySelector('#video-panel').hidden=true;}this.dispatchEvent(new Event('pause'));}
  pause(){this._token++;this.controller?.abort();this.wanted=false;if(this.mode==='local')this.local.pause();else{this.player?.pauseVideo?.();this._paused=true;this.dispatchEvent(new Event('pause'));}}
  get elapsedSinceStart(){return Math.max(0,this.currentTime-(this.mode==='video'?this.trim.start:0));}
  updateTrim(){
    const fmt=n=>`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`;
    document.querySelector('#trim-status').textContent=this.trim.start||this.trim.end?`${fmt(this.trim.start)} → ${this.trim.end?fmt(this.trim.end):'конец записи'}`:'Вся запись';
    for(const id of ['trim-start','trim-end','trim-reset'])document.getElementById(id).disabled=!this.videoId;
  }
  setBoundary(edge){
    if(!this.videoId)return;const value=Math.floor(this.currentTime);
    if((edge==='start'&&this.trim.end&&value>=this.trim.end-1)||(edge==='end'&&value<=this.trim.start+1)){document.querySelector('#trim-status').textContent='Конец должен быть позже начала хотя бы на 2 секунды.';return;}
    this.trim[edge]=value;this.saveTrim();
  }
  saveTrim(){try{localStorage.setItem('volna.trim.'+this.videoId,JSON.stringify(this.trim));}catch{}this.trimEnded=false;this.updateTrim();}
  readTrim(id){this.videoId=id;this.trim={start:0,end:0};try{const v=JSON.parse(localStorage.getItem('volna.trim.'+id));if(v&&Number.isFinite(v.start)&&v.start>=0&&Number.isFinite(v.end)&&(v.end===0||v.end>v.start+1))this.trim=v;}catch{}this.trimEnded=false;this.updateTrim();}
  async resolve(source,signal){
    const key='volna.match.v3.'+new URL(source).pathname;let entry=this.matches.get(key);
    if(!entry)try{entry=JSON.parse(localStorage.getItem(key));}catch{}
    if(entry&&Date.now()-entry.at<86400000&&/^[\w-]{11}$/.test(entry.match?.videoId||''))return entry.match;
    const response=await fetch(source,{signal});if(!response.ok){let message='Сервис поиска записи сейчас недоступен. Попробуй позже.';if(response.status===404||response.status===400)message='Не найдена запись, совпадающая с песней. Можно выбрать её по ссылке YouTube.';throw Error(message);}const match=await response.json();
    if(!/^[\w-]{11}$/.test(match.videoId||''))throw Error('Неверный ответ источника');
    entry={at:Date.now(),match};this.matches.set(key,entry);try{localStorage.setItem(key,JSON.stringify(entry));}catch{}return match;
  }
  startMatch(match){
    this.error=null;this.readTrim(match.videoId);
    document.querySelector('#video-meta').textContent=match.title+' · '+match.channel;
    document.querySelector('#video-link').href='https://www.youtube.com/watch?v='+match.videoId;
    document.querySelector('#video-link').textContent='Открыть на YouTube ↗';
    this.player.loadVideoById({videoId:match.videoId,startSeconds:this.trim.start});this.muted=this._muted;
  }
  handlePlayerError(event){
    if(this.mode!=='video'||!this.loadedSource||!this.wanted)return;
    const actual=event.target?.getVideoData?.()?.video_id;if(actual&&actual!==this.videoId)return;
    const code=Number(event.data);this._paused=true;
    if([100,101,150].includes(code)){
      this.failedVideos.add(this.videoId);
      const next=!this.isManual&&this.activeMatches.find(m=>!this.failedVideos.has(m.videoId));
      if(next){this.startMatch(next);return;}
    }
    const messages={2:'YouTube отклонил ссылку на запись. Выбери другое видео.',5:'YouTube не смог запустить это видео. Попробуй открыть его по ссылке.',100:'Эта запись удалена или закрыта на YouTube. Выбери другую запись.',101:'Автор запретил воспроизведение этой записи на других сайтах. Открой её на YouTube.',150:'Автор запретил воспроизведение этой записи на других сайтах. Открой её на YouTube.',153:'YouTube не подтвердил адрес сайта. Обнови страницу или открой запись на YouTube.'};
    this.error={code:4,providerCode:code,message:messages[code]||'YouTube не разрешил воспроизведение записи. Открой её по ссылке или выбери другую.'};
    try{const key='volna.match.v3.'+new URL(this._src).pathname;localStorage.removeItem(key);this.matches.delete(key);}catch{}
    this.loadedSource='';document.querySelector('#video-meta').textContent=this.error.message;this.dispatchEvent(new Event('error'));
  }
  async ensurePlayer(){
    if(this.playerReady)return this.playerReady;
    this.playerReady=new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Плеер YouTube не загрузился')),18000);
      const create=()=>{this.player=new YT.Player('youtube-player',{width:'100%',height:203,playerVars:{playsinline:1,origin:location.origin,controls:0,fs:0,iv_load_policy:3,hl:'ru'},events:{
        onReady:()=>{clearTimeout(timer);this.player.setVolume(this._volume*100);resolve(this.player);},
        onStateChange:e=>{if(this.mode!=='video'||!this.loadedSource||!this.wanted)return;this._paused=e.data!==1;if(e.data===1){this._duration=this.player.getDuration();this.dispatchEvent(new Event('loadedmetadata'));this.dispatchEvent(new Event('play'));}else if(e.data===0){this.dispatchEvent(new Event('pause'));this.dispatchEvent(new Event('ended'));}else if(e.data===2)this.dispatchEvent(new Event('pause'));},
        onError:event=>this.handlePlayerError(event),
        onAutoplayBlocked:()=>{if(!this.loadedSource||!this.wanted)return;this._paused=true;document.querySelector('#video-meta').textContent='Нажми кнопку воспроизведения внизу, чтобы включить звук.';this.dispatchEvent(new Event('pause'));}
      }});};
      if(window.YT?.Player)create();else{window.onYouTubeIframeAPIReady=create;const script=document.createElement('script');script.src='https://www.youtube.com/iframe_api';script.onerror=()=>{clearTimeout(timer);reject(new Error('YouTube недоступен'));};document.head.append(script);}
    }).catch(error=>{this.playerReady=null;throw error;});return this.playerReady;
  }
  async play(){
    const token=++this._token;this.controller?.abort();this.wanted=true;if(this.mode==='local')return this.local.play();
    const source=this._src;if(!source)throw new Error('Нет записи');
    document.querySelector('#video-panel').hidden=false;
    try{
      if(this.loadedSource===source){await this.ensurePlayer();if(token!==this._token||!this.wanted)return;if(this.trimEnded||this.currentTime<(this.trim.start||0))this.currentTime=this.trim.start||0;this.player.playVideo();return;}
    document.querySelector('#video-link').href='https://www.youtube.com/results?search_query='+encodeURIComponent((this.track?.artist||'')+' '+(this.track?.title||''));
    document.querySelector('#video-link').textContent='Найти запись на YouTube ↗';
    document.querySelector('#video-meta').textContent='Подбираем запись…';
      const playerReady=this.ensurePlayer().then(player=>({player}),error=>({error}));
      this.controller=new AbortController();const controller=this.controller;const timer=setTimeout(()=>controller.abort(),45000);
      let match;let manual=this.manualId;this.manualId=null;try{manual=manual||localStorage.getItem('volna.video.'+new URL(source).pathname);}catch{}
      try{if(manual)match={videoId:manual,title:this.track?.title||'Выбранное видео',channel:'Запись выбрана тобой'};else match=await this.resolve(source,controller.signal);}finally{clearTimeout(timer);}
      if(token!==this._token||!this.wanted)return;
      if(!/^[\w-]{11}$/.test(match.videoId))throw Error('Неверный ответ источника');
      const ready=await playerReady;if(ready.error)throw ready.error;if(token!==this._token||!this.wanted)return;
      this.isManual=!!manual;this.activeMatches=[match,...(Array.isArray(match.candidates)?match.candidates:[])].filter((m,i,all)=>/^[\w-]{11}$/.test(m?.videoId||'')&&all.findIndex(x=>x.videoId===m.videoId)===i&&!this.failedVideos.has(m.videoId)).slice(0,3);
      if(!this.activeMatches.length)throw Error('Ни одна найденная запись не доступна в плеере. Открой песню на YouTube или выбери другую запись.');
      this.loadedSource=source;this.startMatch(this.activeMatches[0]);
    }catch(error){if(token!==this._token||!this.wanted)return;this._paused=true;this.error={code:4,message:(error.name==='AbortError'?'Поиск записи занял слишком долго. Попробуй ещё раз.':error.message)||'Не удалось загрузить запись. Попробуй ещё раз.'};document.querySelector('#video-meta').textContent=this.error.message;this.dispatchEvent(new Event('pause'));this.dispatchEvent(new Event('error'));throw error;}
  }
}
