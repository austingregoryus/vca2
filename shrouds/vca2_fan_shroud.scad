// SPDX-License-Identifier: CC-BY-4.0
// =====================================================================
//  Intel VCA2 (VCA1585LMV)  ->  80 / 92 / 120 mm fan shroud
//  Friction-fit sleeve over the REAR end of the card (the end with the
//  grille and the two 12 V AUX connectors), lofting to a standard
//  80 mm fan flange.  Fan blows INTO the card (Intel "standard" airflow:
//  in at the rear end, out through the PCIe I/O bracket).
//
//  Socket fit copies the end of "Fan-mount-2x40mm-v1.4.stl" that grips
//  the VCA:  96.0 x 40.0 mm inside.  (Card shell = 96.3 x 38.69 nominal.)
//
//  Axes:  X = card thickness (38.69),  Y = card height (96.3),
//         Z = insertion / airflow axis.  Z=0 is the socket mouth.
//  Print flange-down (Z up), no supports needed.
// =====================================================================

// ---------- socket (the part that slips over the card) ----------
sock_h        = 96.0;   // inner, card-height direction (96.0 = same grip as the 40 mm mount)
sock_w        = 40.0;   // inner, card-thickness direction (before ribs)
sock_depth    = 25;     // how far the card shell goes in
wall          = 2.0;    // wall thickness in the socket
mouth_chamfer = 1.0;    // lead-in at the mouth

// crush ribs on the two wide faces -> take up the 1.3 mm width slack.
// 0 = off (exact copy of the 40 mm mount's loose-width fit)
rib_h         = 0.5;    // rib height (40.0 - 2*0.5 = 39.0 => 0.3 mm interference on 38.69)
rib_w         = 2.0;
ribs_per_face = 3;

// ---------- cable exits for the 2x3 / 2x4 12 V AUX plugs ----------
// Connectors sit ~13-15 mm in from each edge of the 96 mm height,
// centred on the card thickness, recessed inside the shell.
cable_slots   = true;
slot_side     = "edge"; // "edge" = wires exit through the card's top/bottom edge walls
                        // "face" = both wires exit through one wide face (flip the
                        //          shroud 180 deg to pick which face)
slot_w        = 22;     // EDGE mode: slot width across the card thickness (2x4 plug ~18 wide)
slot_depth    = 40;     // from the mouth, along Z (covers connector recess + plug + bend)
slot_edge_in  = 1.0;    // how far the cut reaches inward past the inner wall
face_slot_len = 28;     // FACE mode: slot length along the card height, from each edge
face_slot_gap = 2;      // FACE mode: wall left at the corner before the slot starts

// ---------- extender-bracket clearance (plug end only) ----------
// The card's L-shaped support bracket has a vertical plate in the plane of
// the card end that sticks out past one edge.  Opening the short wall on
// that edge for the socket depth lets the shroud slide on with the bracket
// still fitted; the plate then sits in the gap.  Bracket goes on the -Y side.
bracket_clear = false;
bracket_extra = 3;      // how far past the socket floor the opening runs
bracket_w     = 0;      // opening width across the card thickness.
                        //   0  = remove the whole edge wall (always fits)
                        //   >0 = notch only, leaving a corner tab each side
                        //        for a tighter grip (measure your plate first)
bracket_off   = 0;      // notch centre offset across the thickness, +/- mm

// ---------- fan size: 80, 92 or 120 ----------
fan           = 80;     // <-- change to 92 or 120

// per-size table:  [aperture, flange square, hole pitch, corner radius, loft length]
fan_tab = fan == 120 ? [116, 122, 105,  6, 70] :
          fan ==  92 ? [ 88,  94, 82.5, 5, 55] :
                       [ 80,  85, 71.5, 4, 45];

fan_size      = fan_tab[0];   // aperture
flange_size   = fan_tab[1];
hole_pitch    = fan_tab[2];
flange_corner = fan_tab[3];
trans_len     = fan_tab[4];   // length of the rectangle -> square transition
flange_t      = 3.5;
hole_d        = 4.3;    // M4 clearance / self-tapping fan screws

// ---------- loft profile ----------
// "straight" = v1: one hull from the socket box to the flange (the 9 original STLs).
// "bell"     = v2: sliced loft with a uniform wall.  Width/height grow linearly
//              after a C1 ease-in at the socket (no corner for pull-mode flow
//              to separate at); corner radius rounds out on 1-(1-t)^2.
//              At fan=92, loft_len=110: equivalent-cone half-angle 6.8 deg,
//              wide-face wall 13.3 deg, corner 13.5 deg.
loft          = "straight";
loft_len      = 0;      // 0 = table value; v2 uses 110 at fan=92 (1.2 fan diameters)
loft_ease     = 0.15;   // bell: fraction of the loft spent easing in from the socket
loft_slices   = 48;     // bell: hull segments
loft_wall     = 2.0;    // bell: wall thickness through the loft

$fn = 96;

// ---------------------------------------------------------------------
sock_ow = sock_w + 2*wall;
sock_oh = sock_h + 2*wall;
z_trans = sock_depth;
trans_len_used = loft_len > 0 ? loft_len : trans_len;
z_flange = sock_depth + trans_len_used;
total_len = z_flange + flange_t;

module rrect(w, h, r) {
    offset(r) offset(-r) square([w, h], center = true);
}

module slab(z, w, h, r = 1) {
    translate([0, 0, z]) linear_extrude(0.01) rrect(w, h, r);
}

// ---------- bell loft ----------
// cross-section of the air path at loft fraction t (0 = socket, 1 = fan)
function bell_ease(t) = let(e = loft_ease, k = 1/(1 - e/2))
    k * (t < e ? t*t/(2*e) : t - e/2);
function bell_sec(t) = let(
    s = bell_ease(t),
    w = sock_w + (fan_size - sock_w) * s,
    h = sock_h + (fan_size - sock_h) * s,
    r = min(0.5 + (fan_size/2 - 0.5) * (1 - (1 - t)*(1 - t)), w/2, h/2))
    [w, h, r];

// rounded rectangle as a hull of corner circles; stays valid at r = w/2 (a circle)
module sec2d(w, h, r) {
    hull() for (sx = [-1, 1], sy = [-1, 1])
        translate([sx*(w/2 - r), sy*(h/2 - r)]) circle(r = max(r, 0.01));
}

// grow = 0 for the air path, loft_wall for the skin (a true offset of the air path)
module bell_slab(t, grow, z_extra = 0) {
    p = bell_sec(t);
    translate([0, 0, z_trans + t*trans_len_used + z_extra])
        linear_extrude(0.01) sec2d(p[0] + 2*grow, p[1] + 2*grow, p[2] + grow);
}

module bell_loft(grow, z_extra = 0) {
    for (i = [0 : loft_slices - 1])
        hull() {
            bell_slab(i/loft_slices, grow, z_extra);
            bell_slab((i + 1)/loft_slices, grow, z_extra);
        }
}

// outer skin: socket box -> loft -> flange
module outer() {
    // socket
    translate([0, 0, 0]) linear_extrude(sock_depth + 0.01) rrect(sock_ow, sock_oh, 2);
    // loft
    if (loft == "bell")
        bell_loft(loft_wall);
    else
        hull() {
            slab(z_trans, sock_ow, sock_oh, 2);
            slab(z_flange - 0.01, flange_size, flange_size, flange_corner);
        }
    // flange
    translate([0, 0, z_flange - 0.01]) linear_extrude(flange_t + 0.01) rrect(flange_size, flange_size, flange_corner);
}

// inner air path: socket pocket -> loft -> round aperture
module inner() {
    // socket pocket (with lead-in chamfer at the mouth)
    translate([0, 0, -1]) linear_extrude(sock_depth + 1 + 0.01) square([sock_w, sock_h], center = true);
    translate([0, 0, -0.01]) linear_extrude(mouth_chamfer, scale = [sock_w/(sock_w + 2*mouth_chamfer), sock_h/(sock_h + 2*mouth_chamfer)])
        square([sock_w + 2*mouth_chamfer, sock_h + 2*mouth_chamfer], center = true);
    // loft rectangle -> circle
    if (loft == "bell")
        bell_loft(0, -0.01);
    else
        hull() {
            slab(z_trans - 0.01, sock_w, sock_h, 0.5);
            translate([0, 0, z_flange]) cylinder(d = fan_size, h = 0.01);
        }
    // aperture through flange
    translate([0, 0, z_flange - 0.01]) cylinder(d = fan_size, h = flange_t + 1);
}

module fan_holes() {
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx*hole_pitch/2, sy*hole_pitch/2, z_flange - 1]) cylinder(d = hole_d, h = flange_t + 2);
}

module ribs() {
    if (rib_h > 0)
        for (sx = [-1, 1])
            for (i = [0 : ribs_per_face - 1]) {
                y = -sock_h/2 + (i + 0.5) * sock_h/ribs_per_face;
                // rib runs from just past the chamfer to the socket floor, tapered lead-in
                translate([sx*(sock_w/2 - rib_h/2), y, mouth_chamfer + 1])
                    hull() {
                        cube([rib_h, rib_w, 0.01], center = true);
                        translate([-sx*rib_h/2, 0, 0]) cube([0.01, rib_w, 0.01], center = true);
                        translate([0, 0, sock_depth - mouth_chamfer - 1]) cube([rib_h, rib_w, 0.01], center = true);
                    }
            }
}

module cable_slot_cuts() {
    if (cable_slots && slot_side == "edge")
        for (sy = [-1, 1])
            // remove the short wall (card top / bottom edge) from the mouth to
            // slot_depth, wide enough for the plug + cable
            translate([-slot_w/2, sy > 0 ? sock_h/2 - slot_edge_in : -70, -1])
                cube([slot_w, 70 - (sock_h/2 - slot_edge_in), slot_depth + 1]);
    if (cable_slots && slot_side == "face")
        for (sy = [-1, 1])
            // remove a window in the +X wide face next to each corner.
            // bell: the rounding corner curves inside x = sock_w/2 by the
            // slot end, so cut from the centreline (pocket is void there)
            // instead of leaving a feather-thin sliver at the window corner
            translate([loft == "bell" ? 0 : sock_w/2 - slot_edge_in,
                       sy > 0 ? sock_h/2 - face_slot_gap - face_slot_len : -sock_h/2 + face_slot_gap,
                       -1])
                cube([70, face_slot_len, slot_depth + 1]);
}

module bracket_cut() {
    if (bracket_clear) {
        w = bracket_w > 0 ? bracket_w : sock_w + 2*wall + 2;
        translate([bracket_off - w/2, -70, -1])
            cube([w, 70 - sock_h/2 + slot_edge_in, sock_depth + bracket_extra + 1]);
    }
}

module shroud() {
    difference() {
        union() {
            difference() { outer(); inner(); }
            ribs();
        }
        fan_holes();
        cable_slot_cuts();
        bracket_cut();
    }
}

shroud();

echo(str("fan ", fan, " : total length = ", total_len, " mm ; socket inner = ", sock_w - 2*rib_h, " x ", sock_h, " ; flange ", flange_size, " sq"));
