Face and eye detection data for OpenCV.

haarcascade_frontalface_default.xml
haarcascade_eye.xml

These two files are part of OpenCV and are copied here unchanged, including
the Intel License Agreement notice each one carries in its own header. That
licence allows redistribution provided the notice is kept, which it is.

They are here because they are not always installed alongside OpenCV itself.
On Debian and Ubuntu the Python bindings come from python3-opencv while the
detection data is in a separate opencv-data package that nothing depends on,
so a perfectly normal install can end up with OpenCV present and no face
detection at all. Every effect that looks for a face would then quietly do
nothing. The app prefers whatever the system has and falls back to these.
