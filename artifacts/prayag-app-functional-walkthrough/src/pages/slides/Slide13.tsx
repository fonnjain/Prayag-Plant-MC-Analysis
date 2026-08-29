import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide13() {
  return (
    <SlideShell dark>
      <Header section="13 / FOLLOW UP" title="Follow Up closes the plan-to-actual loop" />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[3.5vh]">
        <div className="panel-dark h-[17vh]"><div className="label">01</div><div className="body-copy mt-[1.7vh]">Open the latest frozen or finalized run</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">02</div><div className="body-copy mt-[1.7vh]">Compare planned production with actual production</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">03</div><div className="body-copy mt-[1.7vh]">Review variance and adherence thresholds</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">04</div><div className="body-copy mt-[1.7vh]">Refresh actuals from source records</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">05</div><div className="body-copy mt-[1.7vh]">Download the follow-up workbook</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">06</div><div className="body-copy mt-[1.7vh]">Use the result to guide corrective planning</div></div>
      </div>
      <Footer page="13" />
    </SlideShell>
  );
}