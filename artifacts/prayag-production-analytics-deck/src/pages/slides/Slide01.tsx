import { SlideShell, Tag } from '@/components/Primitives';

const base = import.meta.env.BASE_URL;

export default function Slide01() {
  return (
    <div className="w-screen h-screen overflow-hidden relative ink-slide">
      <img
        src={`${base}industrial-analytics-hero.jpg`}
        crossOrigin="anonymous"
        alt="Manufacturing floor with production analytics"
        className="absolute inset-0 h-full w-full object-cover opacity-60"
      />
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(20,33,43,.98)_0%,rgba(20,33,43,.85)_42%,rgba(20,33,43,.18)_100%)]" />
      <div className="corner-mark" />
      <div className="relative z-10 flex h-full flex-col justify-between px-[7vw] py-[7vh]">
        <div className="flex items-center gap-[1vw]">
          <div className="h-[1.1vh] w-[1.1vh] bg-[var(--amber)]" />
          <div className="kicker">OPERATIONS INTELLIGENCE / 2026</div>
        </div>
        <div className="max-w-[67vw]">
          <Tag dark>Internal product story</Tag>
          <h1 className="hero-headline mt-[2.8vh] max-w-[60vw]">Prayag<br />Production<br />Analytics</h1>
          <div className="signal-line mt-[4vh]" />
          <p className="body-copy mt-[2.3vh] max-w-[43vw]">A trusted operating view for production, efficiency, and Plumbing capacity planning</p>
          <p className="small-copy mt-[1.6vh]">August 2026 product and modelling story</p>
        </div>
        <div className="flex items-end justify-between">
          <div className="label">SOURCE-FIRST / DECISION-READY</div>
          <div className="label">01</div>
        </div>
      </div>
    </div>
  );
}