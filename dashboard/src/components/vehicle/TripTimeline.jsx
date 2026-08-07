export default function TripTimeline({ timeline }) {
  if (!timeline?.length) {
    return <p className="empty-state">No stops on this trip.</p>;
  }

  const format = (minutes) => {
    const target = new Date(Date.now() + minutes * 60000);
    return target.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="timeline">
      {timeline.map((stop, index) => (
        <div className={`timeline-stop ${stop.state}`} key={`${stop.stop}-${index}`}>
          <span className="timeline-dot" aria-hidden="true" />
          <span>
            <span className="timeline-name">{stop.stop}</span>
            <span
              style={{
                display: "block",
                fontSize: 11,
                color: "var(--text-faint)",
                textTransform: "capitalize",
              }}
            >
              {stop.state}
              {!stop.has_coordinates && " · not mapped"}
            </span>
          </span>
          <span className="timeline-eta">
            {stop.state === "departed" ? "—" : format(stop.expected_minutes)}
          </span>
        </div>
      ))}
    </div>
  );
}
