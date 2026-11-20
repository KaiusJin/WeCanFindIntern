import { ViewSwitcher } from "../features/discovery/JobBoard";

export function OpportunityMap() {
  return (
    <div id="tab-heatmap" className="tab-pane" hidden>
      <section className="atlas-workspace">
        <header className="atlas-heading posting-view-heading">
          <h1>Opportunity Map</h1>
          <ViewSwitcher active="map" />
        </header>
        <aside className="atlas-index" aria-label="Regional job distribution">
          <section>
            <h2>Openings by region</h2>
            <div id="heatmap-top-list" className="heatmap-top-list" />
          </section>
          <section className="atlas-key">
            <h2>Color scale</h2>
            <div className="heatmap-legend-bar" />
            <div className="heatmap-legend-labels">
              <span id="heatmap-legend-min">0</span>
              <span>Square-root scale</span>
              <span id="heatmap-legend-max" />
            </div>
            <p className="heatmap-legend-note" id="heatmap-legend-note" />
          </section>
        </aside>
        <div className="atlas-maps">
          {(
            [
              ["us", "United States"],
              ["ca", "Canada"],
            ] as const
          ).map(([code, country]) => (
            <section className="heatmap-block" key={code}>
              <div className="heatmap-block-head">
                <h2>{country}</h2>
                <span className="heatmap-block-total" id={"heatmap-" + code + "-total"} />
              </div>
              <div id={"heatmap-" + code} className="heatmap-map" />
            </section>
          ))}
          <div id="heatmap-tooltip" className="heatmap-tooltip" hidden />
        </div>
      </section>
    </div>
  );
}
