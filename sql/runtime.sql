-- Operational-only extensions. Do not apply to the development image catalog.
CREATE TABLE prediction_media (
  prediction_id TEXT PRIMARY KEY NOT NULL REFERENCES predictions(prediction_id),
  sha256 TEXT NOT NULL CHECK (length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  width INTEGER NOT NULL CHECK (width>0),
  height INTEGER NOT NULL CHECK (height>0)
);
