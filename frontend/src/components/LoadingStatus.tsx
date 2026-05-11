import { TypeAnimation } from "react-type-animation";

export function LoadingStatus({
  active,
  messages,
  fallback,
}: {
  active: boolean;
  messages: string[];
  fallback: string;
}) {
  if (!active || messages.length === 0) {
    return <span>{fallback}</span>;
  }

  const sequence = messages.flatMap((message) => [message, 900]);

  return (
    <TypeAnimation
      key={messages.join("|")}
      sequence={sequence}
      wrapper="span"
      cursor
      repeat={Infinity}
      speed={{ type: "keyStrokeDelayInMs", value: 28 }}
      deletionSpeed={70}
      className="type-status"
    />
  );
}
