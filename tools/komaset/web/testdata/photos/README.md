# Photos test frames

Six photographs the owner's GR DIGITAL took over USB on 2026-11-07 (`~/koma-session-6` to `-13`,
`tether-R00157xx.JPG`), for the Photos e2e tests (`test_e2e_photos.py`) and screenshots (`tools/komaapp/design/`).
The simulated camera serves them as its DCIM contents. MOCK data: nothing here was read from a camera by these tests.

- Downscaled from 3264 × 2448 to 1280 × 960 (`magick -resize 1280x960 -quality 80`); the camera's APP1 block is kept
  whole, so the EXIF exposure tags and the camera's own 160 × 120 IFD1 thumbnail are the camera's.
- Changed with exiftool: `ExifImageWidth`/`ExifImageHeight` to 1280 × 960, and `DateTimeOriginal`, `CreateDate`,
  `ModifyDate` to the time the self-test saved each frame (the camera's clock read 2005:01:01).
- Exposure as the camera wrote it: ExposureTime 1/11, 1/3, 1/4, 1/2, 1/1, 1/1; FNumber 240/100; ISO 154.
