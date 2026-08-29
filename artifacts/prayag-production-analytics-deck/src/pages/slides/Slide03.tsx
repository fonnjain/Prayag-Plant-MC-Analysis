import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide03() {
  return (
    <SlideShell>
      <Header section="03 / SOURCE OF TRUTH" title="Built around the source of truth" />
      <div className="grid grid-cols-[1.15fr_.85fr] gap-[5vw] px-[7vw] pt-[3.5vh]">
        <div className="flex flex-col gap-[2.7vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">Reads production workbooks and planning masters</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Computes dashboard figures from raw, daily-first data</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Keeps plant-specific units explicit: kg, litres, and pieces</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Preserves row-level provenance back to source tabs</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">Never fabricates a ratio when a real baseline is missing</div></div>
        </div>
        <div className="panel relative mt-[1vh] h-[31vh]">
          <div className="label">Evidence chain</div>
          <div className="mt-[4vh] flex items-center gap-[.7vw]">
            <div className="bg-[var(--ink)] px-[1vw] py-[1.2vh] text-[1.1vw] text-[var(--paper)]">WORKBOOKS</div>
            <div className="text-[1.8vw] text-[var(--rust)]">→</div>
            <div className="bg-[var(--steel)] px-[1vw] py-[1.2vh] text-[1.1vw] text-[var(--paper)]">DAILY-FIRST</div>
            <div className="text-[1.8vw] text-[var(--rust)]">→</div>
            <div className="bg-[var(--rust)] px-[1vw] py-[1.2vh] text-[1.1vw] text-[var(--paper)]">DECISION</div>
          </div>
          <div className="absolute bottom-[2vw] left-[2.3vw] right-[2.3vw] signal-line" />
        </div>
      </div>
      <Footer page="03" />
    </SlideShell>
  );
}