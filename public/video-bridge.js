class VideoAudioBridge extends EventTarget {
  constructor(localAudio) {
    super();this.local=localAudio;this._src='';this._paused=true;this._duration=NaN;this._volume=.75;this._muted=false;this._token=0;this.mode='video';this.error=null;
    for(const type of ['play','pause','ended','timeupdate','loadedmetadata','error'])localAudio.addEventListener(type,()=>{if(this.mode==='local')this.dispatchEvent(new Event(type));});
    document.querySelector('#video-manual-form').onsubmit=async event=>{event.preventDefault();const input=document.querySelector('#video-url');try{const url=new URL(input.value);const host=url.hostname.toLowerCase();if(!['youtube.com','www.youtube.com','m.youtube.com','youtu.be'].includes(host)||url.protocol!=='https:')throw Error('URL');const id=host==='youtu.be'?url.pathname.slice(1).split('/')[0]:(url.searchParams.get('v')||url.pathname.match(/^\/(?:shorts|embed)\/([\w-]{11})/)?.[1]);if(!/^[\w-]{11}$/.test(id||''))throw Error('ID');const key=new URL(this._src).pathname;try{localStorage.setItem('volna.video.'+key,id);}catch{}this.manualId=id;this.loadedSource='';await this.play();input.value='';}catch{document.querySelector('#video-meta').textContent='Вставь ссылку на конкретное видео YouTube.';}};
    setInterval(()=>{if(this.mode==='video'&&this.player?.getCurrentTime&&!this._paused)this.dispatchEvent(new Event('timeupdate'));},500);
    document.addEventListener('visibilitychange',()=>{if(document.hidden&&this.mode==='video')this.pause();});
  }
  set src(value){this._src=value;this.mode=value.startsWith('blob:')?'local':'video';if(this.mode==='local')this.local.src=value;}
  get src(){return this._src;}
  get paused(){return this.mode==='local'?this.local.paused:this._paused;}
  get duration(){return this.mode==='local'?this.local.duration:this.player?.getDuration?.()||this._duration;}
  get currentTime(){return this.mode==='local'?this.local.currentTime:this.player?.getCurrentTime?.()||0;}
  set currentTime(value){if(this.mode==='local')this.local.currentTime=value;else this.player?.seekTo?.(value,true);}
  get volume(){return this._volume;}
  set volume(value){this._volume=Math.max(0,Math.min(1,value));this.local.volume=this._volume;this.player?.setVolume?.(this._volume*100);}
  get muted(){return this._muted;}
  set muted(value){this._muted=!!value;this.local.muted=this._muted;if(this._muted)this.player?.mute?.();else this.player?.unMute?.();}
  load(){this._token++;this.controller?.abort();this.loadedSource='';this._paused=true;this.error=null;this.local.pause();this.player?.stopVideo?.();if(this.mode==='local'){this.local.load();document.querySelector('#video-panel').hidden=true;}this.dispatchEvent(new Event('pause'));}
  pause(){this.wanted=false;if(this.mode==='local')this.local.pause();else{this.player?.pauseVideo?.();this._paused=true;this.dispatchEvent(new Event('pause'));}}
  async ensurePlayer(){
    if(this.playerReady)return this.playerReady;
    this.playerReady=new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Плеер YouTube не загрузился')),18000);
      const create=()=>{this.player=new YT.Player('youtube-player',{width:'100%',height:203,playerVars:{playsinline:1,origin:location.origin},events:{
        onReady:()=>{clearTimeout(timer);this.player.setVolume(this._volume*100);resolve(this.player);},
        onStateChange:e=>{if(this.mode!=='video')return;this._paused=e.data!==1;if(e.data===1){this._duration=this.player.getDuration();this.dispatchEvent(new Event('loadedmetadata'));this.dispatchEvent(new Event('play'));}else if(e.data===0){this.dispatchEvent(new Event('pause'));this.dispatchEvent(new Event('ended'));}else if(e.data===2)this.dispatchEvent(new Event('pause'));},
        onError:()=>{this._paused=true;this.error={code:4};this.dispatchEvent(new Event('error'));}
      }});};
      if(window.YT?.Player)create();else{window.onYouTubeIframeAPIReady=create;const script=document.createElement('script');script.src='https://www.youtube.com/iframe_api';script.onerror=()=>{clearTimeout(timer);reject(new Error('YouTube недоступен'));};document.head.append(script);}
    }).catch(error=>{this.playerReady=null;throw error;});return this.playerReady;
  }
  async play(){
    this.wanted=true;if(this.mode==='local')return this.local.play();
    const token=this._token;const source=this._src;if(!source)throw new Error('Нет записи');
    document.querySelector('#video-panel').hidden=false;
    document.querySelector('#video-link').href='https://www.youtube.com/results?search_query='+encodeURIComponent((this.track?.artist||'')+' '+(this.track?.title||''));
    document.querySelector('#video-link').textContent='Найти запись на YouTube ↗';
    document.querySelector('#video-meta').textContent='Подбираем запись…';
    try{
      if(this.loadedSource===source){await this.ensurePlayer();this.player.playVideo();return;}
      this.controller=new AbortController();const timer=setTimeout(()=>this.controller.abort(),45000);
      let match;let manual=this.manualId;this.manualId=null;try{manual=manual||localStorage.getItem('volna.video.'+new URL(source).pathname);}catch{}
      try{if(manual)match={videoId:manual,title:this.track?.title||'Выбранное видео',channel:'Запись выбрана тобой'};else{const response=await fetch(source,{signal:this.controller.signal});if(!response.ok)throw Error('Запись не найдена');match=await response.json();}}finally{clearTimeout(timer);}
      if(token!==this._token||!this.wanted)return;
      if(!/^[\w-]{11}$/.test(match.videoId))throw Error('Неверный ответ источника');
      document.querySelector('#video-meta').textContent=match.title+' · '+match.channel;
      document.querySelector('#video-link').href='https://www.youtube.com/watch?v='+match.videoId;
      document.querySelector('#video-link').textContent='Открыть на YouTube ↗';
      await this.ensurePlayer();if(token!==this._token||!this.wanted)return;
      this.loadedSource=source;this.player.loadVideoById(match.videoId);this.muted=this._muted;
    }catch(error){if(error.name==='AbortError'||token!==this._token)return;this._paused=true;document.querySelector('#video-meta').textContent='Автоматический поиск недоступен. Выбери видео на YouTube и вставь ссылку ниже.';this.dispatchEvent(new Event('pause'));throw error;}
  }
}
