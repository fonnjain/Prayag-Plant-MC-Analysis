import { SlideShell, Tag } from '@/components/Primitives';

export default function Slide17() {
  return (
    <div className="w-screen h-screen overflow-hidden relative ink-slide">
      <div className="corner-mark" />
      <div className="absolute bottom-0 left-0 h-[42vh] w-[48vw] bg-[linear-gradient(135deg,rgba(243,163,74,.16),transparent)]" />
      <div className="relative z-10 flex h-full flex-col justify-between px-[7vw] py-[7vh]">
        <div className="flex items-center justify-between">
          <div className="kicker">17 / DAILY WORKFLOW</div>
          <Tag dark>PRAYAG</Tag>
        </div>
        <div>
          <div className="rule" />
          <h1 className="headline mt-[3vh] max-w-[76vw] text-[5vw]">A practical operator workflow</h1>
          <div className="grid grid-cols-2 gap-[5vw] pt-[4vh]">
            <div className="flex flex-col gap-[1.7vh]">
              <div className="body-copy">Start at Home and choose the operating question</div>
              <div className="body-copy">Read Performance before planning</div>
              <div className="body-copy">Inspect source health and confirmation status</div>
            </div>
            <div className="flex flex-col gap-[1.7vh]">
              <div className="body-copy">Configure or review Machine Planning inputs</div>
              <div className="body-copy">Preview capacity, then create and review a run</div>
              <div className="body-copy">Follow Up against actuals and export the evidence</div>
            </div>
          </div>
          <div className="signal-line mt-[4vh]" />
        </div>
        <div className="flex items-end justify-between">
          <div className="label">DASHBOARD → DECISION → EVIDENCE</div>
          <div className="label">AUGUST 2026</div>
        </div>
      </div>
    </div>
  );
}