import { SlideShell, Tag } from '@/components/Primitives';

export default function Slide17() {
  return (
    <div className="w-screen h-screen overflow-hidden relative ink-slide">
      <div className="corner-mark" />
      <div className="absolute bottom-0 left-0 h-[42vh] w-[48vw] bg-[linear-gradient(135deg,rgba(243,163,74,.16),transparent)]" />
      <div className="relative z-10 flex h-full flex-col justify-between px-[7vw] py-[7vh]">
        <div className="flex items-center justify-between">
          <div className="kicker">17 / CLOSING NOTE</div>
          <Tag dark>PRAYAG</Tag>
        </div>
        <div>
          <div className="rule" />
          <h1 className="hero-headline mt-[3vh] max-w-[73vw]">Prayag makes<br />uncertainty<br /><span className="text-[var(--amber)]">operational.</span></h1>
          <div className="grid grid-cols-2 gap-[5vw] pt-[4vh]">
            <div className="body-copy">One view from production evidence to planning action</div>
            <div className="body-copy">Honest units, baselines, gates, and provenance</div>
          </div>
          <div className="signal-line mt-[4vh]" />
          <div className="body-copy mt-[2vh] max-w-[47vw]">Fallbacks that are visible, comparable, and protectable. A clearer answer to: what can we make, and how confident are we?</div>
        </div>
        <div className="flex items-end justify-between">
          <div className="label">PRODUCTION EVIDENCE → PLANNING ACTION</div>
          <div className="label">AUGUST 2026</div>
        </div>
      </div>
    </div>
  );
}