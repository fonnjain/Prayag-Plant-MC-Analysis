import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide10() {
  return (
    <SlideShell>
      <Header section="10 / CONFIDENCE" title="The confidence question" />
      <div className="grid grid-cols-[.9fr_1.1fr] gap-[6vw] px-[7vw] pt-[4vh]">
        <div className="panel flex h-[36vh] flex-col justify-between">
          <div className="label">Fallback ≠ direct</div>
          <div className="headline text-[3.1vw]">Schedulable<br />is not the<br />same as measured.</div>
          <div className="signal-line" />
        </div>
        <div className="flex flex-col gap-[2.5vh] pt-[1vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">A fallback is schedulable, but it is not equivalent to a measured item standard</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Compare fallback candidates with direct standards where the comparison is valid</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Exclude the compared item from its peer average</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Positive divergence means the fallback is faster and potentially optimistic</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">No same-item direct rate means no invented confidence score</div></div>
        </div>
      </div>
      <Footer page="10" />
    </SlideShell>
  );
}