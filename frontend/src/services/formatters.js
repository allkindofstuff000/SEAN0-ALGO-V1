export const formatTimestamp = (value, options = {}) => {
  if (!value) {
    return "--";
  }

  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return String(value);
  }

  return timestamp.toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: options.showSeconds === false ? undefined : "2-digit",
  });
};

export const formatPercent = (value, digits = 1) => {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) {
    return "0%";
  }
  return `${number.toFixed(digits)}%`;
};

export const titleize = (value) => {
  return String(value ?? "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase());
};

export const numeric = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
};

export const formatNumber = (value, digits = 0) => {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) {
    return "0";
  }
  return Intl.NumberFormat([], {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(number);
};

export const formatCurrency = (value, digits = 0) => {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) {
    return "$0";
  }
  return Intl.NumberFormat([], {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(number);
};

export const formatCompact = (value) => {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) {
    return "0";
  }
  return Intl.NumberFormat([], {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(number);
};

export const toConfidenceTone = (value) => {
  switch (String(value ?? "").toUpperCase()) {
    case "HIGH":
      return "success";
    case "MEDIUM":
      return "warning";
    case "LOW":
      return "danger";
    default:
      return "neutral";
  }
};

export const sessionStrength = (session) => {
  switch (String(session ?? "").toUpperCase()) {
    case "OVERLAP":
      return { label: "Maximum Flow", score: 96 };
    case "LONDON":
      return { label: "Institutional Drive", score: 84 };
    case "NEW_YORK":
      return { label: "High Impulse", score: 81 };
    case "ASIAN":
      return { label: "Measured Flow", score: 58 };
    default:
      return { label: "Unknown", score: 0 };
  }
};
