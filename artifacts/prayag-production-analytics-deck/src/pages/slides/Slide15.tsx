import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide15() {
  return (
    <SlideShell>
      <Header section="15 / PROTECTION" title="Protection without changing direct standards" />
      <div className="grid grid-cols-[1fr_1fr] gap-[5vw] px-[7vw] pt-[3.5vh]">
        <div className="panel">
          <div className="label">Conservative fallback policy</div>
          <div className="body-copy mt-[2.5vh]">Material and overall fallback candidates use conservative lower-quartile peer rates</div>
          <div className="mt-[3vh] flex items-center gap-[1vw]"><div className="stat-number text-[4vw]">P25</div><div className="small-copy max-w-[15vw]">nearest-rank lower quartile</div></div>
        </div>
        <div className="flex flex-col gap-[2.6vh] pt-[1vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">The policy uses nearest-rank P25</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">A configured pipe material rate may lower, but never raise, the conservative peer rate</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Direct item standards and same-item fitting cycle rates stay unchanged</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Capacity impact is exposed before adoption</div></div>
        </div>
      </div>
      <Footer page="15" />
    </SlideShell>
  );
}