// CPU harness that compiles the *reference* OpenDRT.dctl unmodified and evaluates
// it on stdin float triplets, writing float triplets to stdout. Used by bake.py
// to sample the OpenDRT Standard show-look LUT without Nuke or Resolve.
//
// Output is the DCTL's Standard preset at Lp=100, ACEScg input, sRGB-Display
// encoding (eotf=1 => 2.2 power). bake.py recovers exact display-linear Rec.709
// as out**2.2 (OpenDRT clamps to [0,1] before the EOTF, so this inverts exactly).
//
// The reference DCTL is GPLv3 and is NOT vendored; bake.py downloads the pinned
// v1.1.0 file and passes its path via -DOPENDRT_DCTL="...". Build:
//   g++ -O2 -DOPENDRT_DCTL='"/path/to/OpenDRT.dctl"' -o bake_opendrt bake_opendrt.cpp
#include <cstdio>
#include <cmath>
#include <vector>

struct float2 { float x, y; };
struct float3 { float x, y, z; };

static inline float2 make_float2(float x, float y) { float2 r{x, y}; return r; }
static inline float3 make_float3(float x, float y, float z) { float3 r{x, y, z}; return r; }

static inline float3 operator+(float3 a, float3 b){ return make_float3(a.x+b.x,a.y+b.y,a.z+b.z); }
static inline float3 operator-(float3 a, float3 b){ return make_float3(a.x-b.x,a.y-b.y,a.z-b.z); }
static inline float3 operator*(float3 a, float3 b){ return make_float3(a.x*b.x,a.y*b.y,a.z*b.z); }
static inline float3 operator/(float3 a, float3 b){ return make_float3(a.x/b.x,a.y/b.y,a.z/b.z); }
static inline float3 operator+(float3 a, float b){ return make_float3(a.x+b,a.y+b,a.z+b); }
static inline float3 operator-(float3 a, float b){ return make_float3(a.x-b,a.y-b,a.z-b); }
static inline float3 operator*(float3 a, float b){ return make_float3(a.x*b,a.y*b,a.z*b); }
static inline float3 operator/(float3 a, float b){ return make_float3(a.x/b,a.y/b,a.z/b); }
static inline float3 operator+(float a, float3 b){ return make_float3(a+b.x,a+b.y,a+b.z); }
static inline float3 operator-(float a, float3 b){ return make_float3(a-b.x,a-b.y,a-b.z); }
static inline float3 operator*(float a, float3 b){ return make_float3(a*b.x,a*b.y,a*b.z); }
static inline float3 operator/(float a, float3 b){ return make_float3(a/b.x,a/b.y,a/b.z); }
static inline float3& operator+=(float3& a, float3 b){ a=a+b; return a; }
static inline float3& operator-=(float3& a, float3 b){ a=a-b; return a; }
static inline float3& operator*=(float3& a, float3 b){ a=a*b; return a; }
static inline float3& operator/=(float3& a, float3 b){ a=a/b; return a; }
static inline float3& operator+=(float3& a, float b){ a=a+b; return a; }
static inline float3& operator-=(float3& a, float b){ a=a-b; return a; }
static inline float3& operator*=(float3& a, float b){ a=a*b; return a; }
static inline float3& operator/=(float3& a, float b){ a=a/b; return a; }

// ---- DCTL intrinsic shims ----
static inline float _powf(float a, float b){ return powf(a,b); }
static inline float _logf(float a){ return logf(a); }
static inline float _log2f(float a){ return log2f(a); }
static inline float _expf(float a){ return expf(a); }
static inline float _exp2f(float a){ return exp2f(a); }
static inline float _exp10f(float a){ return powf(10.0f,a); }
static inline float _sqrtf(float a){ return sqrtf(a); }
static inline float _fmaxf(float a, float b){ return fmaxf(a,b); }
static inline float _fminf(float a, float b){ return fminf(a,b); }
static inline float _fabs(float a){ return fabsf(a); }
static inline float _atan2f(float a, float b){ return atan2f(a,b); }
static inline float _fmod(float a, float b){ return fmodf(a,b); }

// ---- DCTL keyword + UI macro neutralization ----
#define __DEVICE__ static inline
#define __CONSTANT__ static const
#define DEFINE_UI_PARAMS(...)
#define DEFINE_UI_TOOLTIP(...)

// ---- Input parameter globals (replace the stripped DEFINE_UI_PARAMS) ----
int   in_gamut = 2;                // ACEScg (AP1)
int   in_oetf = 0;                 // Linear
float tn_Lp = 100.0f;              // Display peak luminance
float tn_gb = 0.13f;               // HDR grey boost (inactive at 100 nits)
float pt_hdr = 0.5f;               // HDR purity (inactive at 100 nits)
float tn_Lg = 10.0f;               // Display grey luminance
int   crv_enable = 0;              // No tonescale overlay
int   look_preset = 0;             // Standard
int   tonescale_preset = 0;        // Use look-preset tonescale
int   display_encoding_preset = 1; // sRGB Display: tn_su=2, Rec.709, eotf=1
int   _cwp = 0;                    // Use look-preset creative white
float _cwp_lm = 0.25f;

#ifndef OPENDRT_DCTL
#error "define OPENDRT_DCTL to the path of the pinned OpenDRT.dctl"
#endif
#include OPENDRT_DCTL

int main() {
    std::vector<float> buf(3 * 65536);
    size_t n;
    while ((n = fread(buf.data(), sizeof(float), buf.size(), stdin)) > 0) {
        size_t px = n / 3;
        for (size_t i = 0; i < px; ++i) {
            float3 o = transform(1, 1, 0, 0, buf[3*i], buf[3*i+1], buf[3*i+2]);
            buf[3*i] = o.x; buf[3*i+1] = o.y; buf[3*i+2] = o.z;
        }
        fwrite(buf.data(), sizeof(float), px * 3, stdout);
    }
    return 0;
}
