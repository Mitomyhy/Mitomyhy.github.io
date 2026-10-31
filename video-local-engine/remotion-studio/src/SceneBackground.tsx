import React, {useMemo} from 'react';
import {AbsoluteFill, Img, Loop, OffthreadVideo, staticFile, useVideoConfig} from 'remotion';
import {seeded} from './scene.mjs';
import type {ProductScene, Theme} from './types';

const palettes: Record<Theme, [string, string, string]> = {
  studio: ['#eee7db', '#c3af98', '#faf7f0'],
  christmas: ['#183e31', '#315b42', '#d8bb7c'],
  floral: ['#e5e9d7', '#a7b49a', '#f3dccd'],
  memorial: ['#e2e9ed', '#b6c8d5', '#fff8e7'],
  wedding: ['#f2e9de', '#d5bc9b', '#fff9ef'],
  nursery: ['#e2dfd7', '#beb9a8', '#f6eee0'],
  coastal: ['#dae9e8', '#8fbbb9', '#f6e7cf'],
  rustic: ['#9b8065', '#695541', '#d9c3a1'],
  sports: ['#e4e8e3', '#879a83', '#c9b795'],
};

export const SceneBackground: React.FC<{scene: ProductScene; phase: number; parallaxX: number}> = ({scene, phase, parallaxX}) => {
  const {fps, width, height} = useVideoConfig();
  // AI clips are deliberately a shallow-focus backplate. Local video models
  // can invent lettering even with a negative prompt; defocus prevents those
  // details from competing with the unchanged, sharp product artwork. User
  // supplied static photos retain their original focus.
  const videoBlur = Math.max(24, Math.round(Math.min(width, height) * .045));
  const colors = scene.background.palette ?? palettes[scene.background.theme];
  const points = useMemo(() => {
    const random = seeded(scene.seed);
    return Array.from({length: 30}, (_, i) => ({id: i, x: random() * 1000, y: random() * 1000, size: 3 + random() * 24, alpha: 0.12 + random() * 0.25}));
  }, [scene.seed]);
  const floral = ['floral', 'wedding', 'nursery', 'memorial'].includes(scene.background.theme);
  const holiday = scene.background.theme === 'christmas';
  return <AbsoluteFill style={{backgroundColor: colors[0], overflow: 'hidden'}}>
    {scene.background.videoSrc ? <Loop durationInFrames={Math.max(1, Math.round((scene.background.videoDurationSeconds ?? 6.125) * fps))}><OffthreadVideo muted src={staticFile(scene.background.videoSrc)} style={{width: '100%', height: '100%', objectFit: 'cover', filter: `blur(${videoBlur}px)`, transform: `scale(1.18) translateX(${parallaxX}px)`}}/></Loop> : scene.background.src ? <Img src={scene.background.src} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(1.06) translateX(${parallaxX}px)`}}/> : <>
      <AbsoluteFill style={{background: `radial-gradient(ellipse at 55% 36%, ${colors[2]}, transparent 67%),linear-gradient(135deg, ${colors[0]},${colors[1]})`}}/>
      <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" width="100%" height="100%" style={{position: 'absolute', transform: `scale(1.05) translateX(${parallaxX}px)`}}>
        <defs>
          <filter id="bokeh"><feGaussianBlur stdDeviation="4"/></filter>
          <filter id="soft"><feGaussianBlur stdDeviation="14"/></filter>
          <linearGradient id="desk" x1="0" y1="0" x2="0" y2="1"><stop stopColor={colors[2]} stopOpacity=".25"/><stop offset="1" stopColor={colors[1]} stopOpacity=".65"/></linearGradient>
        </defs>
        <path d="M0 820 Q500 775 1000 820 V1000 H0Z" fill="url(#desk)"/>
        {scene.background.theme === 'rustic' && Array.from({length: 9}, (_, i) => <path key={i} d={`M${i*130-30} 815 L${i*170-180} 1000`} stroke="#493c31" strokeWidth="2" opacity=".13"/>)}
        {holiday && <g fill="#153d2c" opacity=".75" filter="url(#soft)"><path d="M-30 0L210 0 10 360 190 300 5 520 160 470 -20 720Z"/><path d="M1000 0L800 0 990 280 850 240 1000 510Z"/></g>}
        {floral && <g opacity=".5" filter="url(#soft)">
          <path d="M-35 900 Q120 620 190 90 M1030 1000 Q830 750 895 420" fill="none" stroke={colors[1]} strokeWidth="12"/>
          {[0,1,2,3,4,5].map(i => <g key={i} transform={`translate(${20+i*24},${210+i*100}) rotate(${-30+i*18})`}><ellipse rx="35" ry="60" fill={colors[1]}/><ellipse cx="38" cy="28" rx="30" ry="55" fill={colors[2]}/></g>)}
          {[0,1,2,3].map(i => <g key={i} transform={`translate(${930-i*12},${520+i*95})`}><circle r="44" fill={scene.background.theme === 'memorial' ? '#ffffff' : '#ecd9c9'}/><circle cx="24" cy="-18" r="38" fill={colors[2]}/><circle cx="-24" cy="-18" r="38" fill={colors[2]}/></g>)}
        </g>}
        {scene.background.theme === 'coastal' && <g opacity=".2" stroke={colors[1]} fill="none" strokeWidth="3">{[0,1,2,3].map(i => <path key={i} d={`M0 ${120+i*50} Q250 ${70+i*50} 500 ${120+i*50} T1000 ${120+i*50}`}/>)}</g>}
        {scene.background.theme === 'sports' && <g filter="url(#soft)" opacity=".14"><path d="M0 1000L500 300 1000 1000Z" fill="#2f5731"/><circle cx="500" cy="900" r="290" fill="none" stroke="#ffffff" strokeWidth="12"/></g>}
        {scene.background.theme === 'wedding' && <g filter="url(#soft)" opacity=".4" stroke="#bd9a58" fill="none" strokeWidth="5"><circle cx="900" cy="170" r="100"/><circle cx="820" cy="200" r="100"/></g>}
        <g filter="url(#bokeh)" fill={holiday ? '#fff2cb' : colors[2]}>{points.map(p => <circle key={p.id} cx={p.x + Math.sin(phase*.4+p.id)*4} cy={p.y} r={p.size} opacity={p.alpha}/>)}</g>
      </svg>
    </>}
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 35%, rgba(13,24,20,.13) 100%)'}}/>
  </AbsoluteFill>;
};
