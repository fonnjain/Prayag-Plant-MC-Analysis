import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide02() {
  return (
    <SlideShell>
      <Header section="02 / OPERATING VIEW" title="One system for the daily operating question" />
      <div className="grid grid-cols-2 gap-[5vw] px-[7vw] pt-[4vh]">
        <div className="flex flex-col gap-[3.5vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">What did each plant, machine, and segment produce?</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">How efficiently did the fleet run?</div></div>
        </div>
        <div className="flex flex-col gap-[3.5vh]">
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Which numbers are confirmed, provisional, or data-limited?</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Can this month’s Plumbing demand fit the available machine capacity?</div></div>
        </div>
      </div>
      <div className="absolute bottom-[10vh] left-[7vw] right-[7vw]">
        <div className="signal-line" />
        <div className="small-copy mt-[1.5vh] max-w-[39vw]">A single operating surface connects evidence, explanation, and action.</div>
      </div>
      <Footer page="02" />
    </SlideShell>
  );
}