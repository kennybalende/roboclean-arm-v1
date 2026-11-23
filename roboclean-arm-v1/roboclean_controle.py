#!/usr/bin/env python3
"""
RoboClean Arm-V1 — Poste de commande (jumeau numérique + liaison Arduino)

Implémente les formules du dossier d'ingénierie :
  - modèle géométrique inverse du bras (d1, a1, a2, a3, d6 ; ×0,4 en prototype)
  - séquence de commande (tableau 15) : approche rapide/lente, appui, pulvérisation,
    balayage montée (tampon) / descente (raclette), retournement A6, décalage 85 %
  - régulateur PI d'effort numérique (§6.3.3, Te = 5 ms, Kp = 0,452 mm/N)
  - modèle de contact G(z) = 884,8 / (z − 0,7788) (Ke = 4000 N/m, tau = 20 ms)
  - sécurité : STO si Fz > 15 N, arrêt d'urgence
  - réservoirs : cuve de toiture -> tampon embarqué (seuils bas / haut)

Utilisation :
  python roboclean_controle.py                      # interface graphique
  python roboclean_controle.py --proto --port COM3  # prototype relié à l'Arduino
  python roboclean_controle.py --headless           # cycle complet sans interface

Dépendances : bibliothèque standard (tkinter) ; pyserial uniquement pour --port.
Protocole série (115200 bauds) : "Q a1 a2 a3 a4 a5 a6\\n" (angles servo 0-180°)
et "P 0|1\\n" (pompe). Le sketch Arduino correspondant est en fin de fichier.
"""
import argparse
import math
import random
import time

try:
    import serial  # pyserial, optionnel
except ImportError:
    serial = None

DT = 0.005  # période de commande : 5 ms
LIMITE_F = 15.0  # N, seuil STO
CONSIGNE_F = 12.5  # N, consigne industrielle
SEUIL_PROTO = 10.0  # N, arrêt d'appui du prototype
CONTACT = ("PRESS", "SPRAY", "UP", "TURN", "DOWN", "SHIFT")
NOMS = {
    "IDLE": "Repos", "FAST": "Approche rapide", "SLOW": "Approche lente",
    "PRESS": "Appui", "SPRAY": "Pulvérisation", "UP": "Balayage montée (tampon)",
    "TURN": "Retournement A6", "DOWN": "Balayage descente (raclette)",
    "SHIFT": "Décalage latéral", "RET": "Retrait", "DONE": "Cycle terminé",
    "STO": "Arrêt STO",
}


class Reservoir:
    """Réservoir tampon alimenté par gravité depuis la cuve de toiture."""

    def __init__(self, proto):
        if proto:  # bidon 1 L -> réservoir 250 mL (unités : litres)
            self.nom, self.cap, self.bas, self.haut = "Bidon 1 L -> embarqué 250 mL", .25, .05, .24
            self.v, self.source, self.debit = .20, 1.0, .1 / 60
        else:      # cuve 100 L -> tampon 10 L, remplissage 3 L/min
            self.nom, self.cap, self.bas, self.haut = "Cuve toiture 100 L -> tampon 10 L", 10., 2., 9.
            self.v, self.source, self.debit = 9., 100., 3. / 60
        self.ev_ouverte = False

    def update(self, dt):
        if self.v < self.bas:
            self.ev_ouverte = True
        if self.v >= self.haut:
            self.ev_ouverte = False
        if self.ev_ouverte and self.source > 0:
            self.v += self.debit * dt
            self.source -= self.debit * dt


class Robot:
    def __init__(self, proto=False, ke=4000.0, retard=False):
        self.proto, self.ke, self.retard = proto, ke, retard
        self.k = 0.4 if proto else 1.0
        self.reset()

    # ------------------------------------------------------------ géométrie
    def reset(self):
        k = self.k
        self.d1, self.a1, self.L2, self.L3, self.d6 = 400 * k, 25 * k, 315 * k, 365 * k, 80 * k
        self.W, self.wt, self.xg = 500 * k, 350 * k, 560 * k  # fenêtre, raclette, vitre
        self.npass = math.ceil((self.W - self.wt) / (.85 * self.wt)) + 1
        self.passe = 0
        self.y, self.z = self.y_passe(0), -self.W / 2  # z relatif à l'épaule
        self.gap = 150 * k
        self.F = self.Fs = self.Fprec = self.dz = self.e1 = self.zc = 0.0
        self.st, self.a6, self.t, self.spT, self.okT = "IDLE", 0.0, 0.0, 0.0, 0.0
        self.pompe, self.msg, self.hist, self.n = 0, "", [], 0
        self.cells = {}  # (i, j) -> "wet" | "clean"
        self.res = Reservoir(self.proto)
        self.fmax, self.eau0 = 0.0, self.res.v

    def y_passe(self, i):
        return min(-self.W / 2 + self.wt / 2 + i * .85 * self.wt, self.W / 2 - self.wt / 2)

    def ik(self):
        """MGI planaire dans le plan radial : coude en haut. None si hors d'atteinte."""
        xw = self.xg - self.gap - self.d6
        r = math.hypot(xw, self.y) - self.a1
        zw = self.z
        D = (r * r + zw * zw - self.L2 ** 2 - self.L3 ** 2) / (2 * self.L2 * self.L3)
        if abs(D) > 1:
            return None
        t3 = -math.acos(D)
        t2 = math.atan2(zw, r) - math.atan2(self.L3 * math.sin(t3), self.L2 + self.L3 * math.cos(t3))
        return {"q1": math.atan2(self.y, xw), "t2": t2, "t3": t3}

    def joints(self):
        """Angles articulaires q1..q6 en degrés (outil maintenu normal à la vitre)."""
        s = self.ik()
        if not s:
            return None
        d = 180 / math.pi
        return [s["q1"] * d, 90 - s["t2"] * d, -s["t3"] * d, -s["q1"] * d,
                -(s["t2"] + s["t3"]) * d, self.a6]

    def servos(self):
        q = self.joints()
        if not q:
            return None
        return [max(0, min(180, q[5] if i == 5 else 90 + q[i])) for i in range(6)]

    def pose(self):
        """Points (rho, z) épaule, coude, poignet, bout d'outil pour le dessin."""
        s = self.ik()
        if not s:
            return None
        sh = (self.a1, self.d1)
        el = (sh[0] + self.L2 * math.cos(s["t2"]), sh[1] + self.L2 * math.sin(s["t2"]))
        a = s["t2"] + s["t3"]
        wr = (el[0] + self.L3 * math.cos(a), el[1] + self.L3 * math.sin(a))
        return sh, el, wr, (wr[0] + self.d6, wr[1])

    # ------------------------------------------------------------- commande
    def demarrer(self):
        if self.st in ("IDLE", "DONE"):
            self.reset()
            self.st = "FAST"

    def arret(self, raison):
        if self.st != "STO":
            self.st = "STO"
            self.msg = f"STO : {raison}. Couple coupé, freins serrés."

    def peindre(self, genre):
        N = 50
        i0 = int((self.y - self.wt / 2 + self.W / 2) / self.W * N)
        i1 = int((self.y + self.wt / 2 + self.W / 2) / self.W * N)
        j = int((self.W / 2 - self.z) / self.W * N)
        for i in range(max(0, i0), min(N, i1 + 1)):
            self.cells[(i, max(0, min(N - 1, j)))] = genre

    def step(self):
        if self.st == "STO":
            return
        self.t += DT
        self.n += 1
        a = math.exp(-DT / 0.02)
        kes = 3300.0 if self.proto else self.ke
        contact = self.st in CONTACT
        # --- capteur d'effort : état du contact + perturbation (balancement nacelle)
        pert = .35 * math.sin(2 * math.pi * .8 * self.t) + .12 * (random.random() - .5) if contact else 0
        fo = max(0.0, self.Fs + pert)
        fm = self.Fprec if self.retard else fo
        self.Fprec, self.F = fo, (fo if contact else 0.0)
        if contact:
            if not self.proto:  # PI : dz(k) = dz(k-1) + 0,452 e(k) - 0,352 e(k-1)  [mm]
                e = CONSIGNE_F - fm
                self.dz = min(8.0, max(0.0, self.dz + .452 * e - .352 * self.e1))
                self.e1, self.zc = e, self.dz
            elif self.st == "PRESS" and fm < SEUIL_PROTO:
                self.zc += 1.5 * DT  # rampe d'appui du prototype (mm)
            self.Fs = a * self.Fs + kes * (1 - a) * self.zc / 1000.0
        else:
            self.Fs = self.F = self.dz = self.e1 = self.zc = 0.0
        self.fmax = max(self.fmax, self.F)
        if self.F > LIMITE_F:
            return self.arret(f"effort {self.F:.1f} N > {LIMITE_F:.0f} N")

        self.res.update(DT)
        self.pompe = 0
        v, k, W = 30 * self.k * DT, self.k, self.W
        st = self.st
        if st == "FAST":
            self.gap -= 60 * k * DT
            if self.gap <= 30:
                self.gap, self.st = 30, "SLOW"
        elif st == "SLOW":
            self.gap -= 10 * DT
            if self.gap <= 0:
                self.gap, self.st, self.okT = 0, "PRESS", 0
        elif st == "PRESS":
            ok = fm >= SEUIL_PROTO if self.proto else abs(CONSIGNE_F - fm) < .6
            self.okT = self.okT + DT if ok else 0
            if ok and (self.proto or self.okT > .3):
                self.st, self.spT = "SPRAY", (1.5 if self.proto else 2.0)
        elif st == "SPRAY":
            self.pompe = 1
            duree = 1.5 if self.proto else 2.0
            self.res.v -= .45 * (self.wt / 1e3) * (W / 1e3) * DT / duree  # 0,45 L/m2
            self.spT -= DT
            if self.spT <= 0:
                self.st = "UP"
        elif st == "UP":
            self.z += v
            if self.n % 5 == 0:
                self.peindre("wet")
            if self.z >= W / 2:
                self.z, self.st = W / 2, "TURN"
        elif st == "TURN":
            haut = self.z > 0
            self.a6 += (150 if haut else -150) * DT
            if haut and self.a6 >= 180:
                self.a6, self.st = 180, "DOWN"
            elif not haut and self.a6 <= 0:
                self.a6, self.st, self.spT = 0, "SPRAY", 1.5
        elif st == "DOWN":
            self.z -= v
            if self.n % 5 == 0:
                self.peindre("clean")
            if self.z <= -W / 2:
                self.z = -W / 2
                if self.passe + 1 < self.npass:
                    self.passe += 1
                    self.st = "SHIFT"
                else:
                    self.st = "RET"
        elif st == "SHIFT":
            cible = self.y_passe(self.passe)
            self.y = min(cible, self.y + 60 * k * DT)
            if self.y >= cible:
                self.st = "TURN"
        elif st == "RET":
            self.gap += 80 * k * DT
            self.a6 = 0
            if self.gap >= 150 * k:
                self.gap, self.st = 150 * k, "DONE"
                self.msg = f"Cycle terminé : {self.npass} passes."
        if self.n % 5 == 0:
            self.hist.append(self.F)
            del self.hist[:-600]

    def rapport(self):
        """Résumé du cycle, au format attendu par POST /api/cycles."""
        return {"mode": "proto" if self.proto else "ind", "status": "STO" if self.st == "STO" else "DONE",
                "duration_s": round(self.t, 2), "passes": min(self.passe + 1, self.npass),
                "fmax": round(self.fmax, 2), "water_ml": round(max(0.0, self.eau0 - self.res.v) * 1000, 1),
                "source": "python", "notes": self.msg if self.st == "STO" else ""}

    def telemetrie(self):
        q = self.joints()
        qs = "hors d'atteinte" if not q else " / ".join(f"{x:6.1f}°" for x in q)
        lignes = [
            f"État        : {NOMS[self.st]}",
            f"Passe       : {self.passe + 1} / {self.npass}",
            f"Fz mesurée  : {self.F:6.2f} N",
            f"Ultrasons   : {self.gap / 10:6.1f} cm",
        ]
        if self.proto:
            adc = 1023 * 10 / (10 + 15 / max(self.F, .01))  # FSR : R(10 N) = 1,5 kΩ
            lignes.append(f"FSR (CAN)   : {adc:6.0f} / seuil 890")
        else:
            lignes.append(f"PI  Δz      : {self.dz:6.2f} mm")
        r = self.res
        lignes += [
            f"q1..q6      : {qs}",
            f"Pompe       : {'ON' if self.pompe else 'off'}",
            f"Tampon      : {r.v * 1000:7.0f} mL  (EV {'ouverte' if r.ev_ouverte else 'fermée'})",
            f"Source      : {r.source:7.2f} L",
        ]
        return "\n".join(lignes)


def envoyer_api(url, rapport):
    """Enregistre un cycle sur le serveur de gestion (npm start). Silencieux si injoignable."""
    import json
    import urllib.request
    req = urllib.request.Request(url.rstrip("/") + "/api/cycles", json.dumps(rapport).encode(),
                                 {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
        print("Cycle enregistré sur", url)
    except Exception as ex:  # noqa: BLE001
        print("Serveur de gestion injoignable :", ex)


class LiaisonSerie:
    def __init__(self, port):
        if serial is None:
            raise RuntimeError("pyserial manquant : pip install pyserial")
        self.s = serial.Serial(port, 115200, timeout=0)
        time.sleep(2)  # reset de l'Arduino à l'ouverture

    def envoyer(self, robot):
        ang = robot.servos()
        if ang:
            self.s.write(("Q " + " ".join(f"{x:.1f}" for x in ang) + f"\nP {robot.pompe}\n").encode())

    def fermer(self):
        self.s.close()


# ------------------------------------------------------------------ interface
def lancer_gui(proto, port, api=None):
    import tkinter as tk
    from tkinter import ttk

    BG, INK, AC, OK, ER, WR, LN = "#eaf1f2", "#12262b", "#0b7f96", "#2a8556", "#c0392b", "#b86d10", "#cbd9db"
    root = tk.Tk()
    root.title("RoboClean Arm-V1 — Poste de commande")
    root.configure(bg=BG)
    E = {"robot": Robot(proto), "pause": False, "liaison": None, "cases": {}}
    if port:
        try:
            E["liaison"] = LiaisonSerie(port)
        except Exception as ex:  # noqa: BLE001
            print("Liaison série impossible :", ex)

    haut = ttk.Frame(root)
    haut.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=6)
    mode = tk.StringVar(value="proto" if proto else "ind")
    vit = tk.StringVar(value="4")
    retard = tk.BooleanVar()
    ttk.Combobox(haut, textvariable=mode, values=["ind", "proto"], width=7, state="readonly").pack(side="left")
    ttk.Combobox(haut, textvariable=vit, values=["1", "4", "10"], width=4, state="readonly").pack(side="left", padx=4)
    badge = tk.Label(haut, text="Repos", bg=AC, fg="white", padx=10, font=("Segoe UI", 11, "bold"))
    badge.pack(side="right")

    def nouveau(*_):
        E["robot"] = Robot(mode.get() == "proto", ke.get(), retard.get())
        for it in E["cases"].values():
            fv.delete(it)
        E["cases"].clear()
        jy.config(from_=-(E["robot"].W - E["robot"].wt) / 2, to=(E["robot"].W - E["robot"].wt) / 2)
        jz.config(from_=-E["robot"].W / 2, to=E["robot"].W / 2)

    def pause():
        E["pause"] = not E["pause"]
        bp.config(text="Reprendre" if E["pause"] else "Pause")

    ttk.Button(haut, text="Lancer le cycle", command=lambda: E["robot"].demarrer()).pack(side="left", padx=4)
    bp = ttk.Button(haut, text="Pause", command=pause)
    bp.pack(side="left")
    tk.Button(haut, text="ARRÊT D'URGENCE", bg=ER, fg="white", command=lambda: E["robot"].arret("arrêt d'urgence manuel")
              ).pack(side="left", padx=6)
    ttk.Button(haut, text="Réarmer", command=nouveau).pack(side="left")
    ttk.Button(haut, text="Niveau bas tampon",
               command=lambda: setattr(E["robot"].res, "v", E["robot"].res.bas * .9)).pack(side="left", padx=4)
    ttk.Checkbutton(haut, text="Retard capteur", variable=retard,
                    command=lambda: setattr(E["robot"], "retard", retard.get())).pack(side="left")

    sv = tk.Canvas(root, width=520, height=420, bg="white", highlightthickness=1, highlightbackground=LN)
    sv.grid(row=1, column=0, padx=8, pady=4)
    fv = tk.Canvas(root, width=300, height=300, bg="#e4f2f5", highlightthickness=1, highlightbackground=LN)
    fv.grid(row=1, column=1, padx=8, pady=4, sticky="n")
    ch = tk.Canvas(root, width=520, height=170, bg="white", highlightthickness=1, highlightbackground=LN)
    ch.grid(row=2, column=0, padx=8, pady=4)
    tele = tk.Label(root, justify="left", anchor="nw", font=("Consolas", 10), bg=BG, fg=INK)
    tele.grid(row=2, column=1, sticky="nw", padx=8)
    msg = tk.Label(root, bg=BG, fg=WR, anchor="w")
    msg.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8)

    bas = ttk.Frame(root)
    bas.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=6)
    ke = tk.DoubleVar(value=4000)
    for txt, var, a, b in (("Ke (N/m)", ke, 1000, 12000),):
        ttk.Label(bas, text=txt).pack(side="left")
        tk.Scale(bas, variable=var, from_=a, to=b, orient="horizontal", length=140, resolution=500,
                 command=lambda v: setattr(E["robot"], "ke", float(v))).pack(side="left", padx=6)
    ttk.Label(bas, text="Y (mm)").pack(side="left")
    jy = tk.Scale(bas, orient="horizontal", length=140)
    jy.pack(side="left")
    ttk.Label(bas, text="Z (mm)").pack(side="left")
    jz = tk.Scale(bas, orient="horizontal", length=140)
    jz.pack(side="left")
    mode.trace_add("write", nouveau)
    nouveau()
    last = [time.perf_counter(), 0.0]

    def dessiner(r):
        s = .6 / r.k
        X, Z = (lambda v: 50 + v * s), (lambda v: 400 - v * s)
        sv.delete("all")
        sv.create_line(0, 400, 520, 400, fill=LN, width=2)
        sv.create_rectangle(X(-60 * r.k), Z(300 * r.k), X(60 * r.k), Z(0), fill="#8fa8ad", outline="")
        sv.create_rectangle(X(r.xg), Z(r.d1 + r.W / 2), X(r.xg) + 8, Z(r.d1 - r.W / 2), fill="#9fd3de", outline=AC)
        p = r.pose()
        if p:
            sh, el, wr, tip = p
            for (a, b), w in zip(((sh, el), (el, wr), (wr, tip)), (16, 12, 7)):
                sv.create_line(X(a[0]), Z(a[1]), X(b[0]), Z(b[1]), width=w, fill=INK, capstyle="round")
            for q in (sh, el, wr):
                sv.create_oval(X(q[0]) - 6, Z(q[1]) - 6, X(q[0]) + 6, Z(q[1]) + 6, fill=WR, outline="")
            sv.create_rectangle(X(tip[0]) - 3, Z(tip[1]) - 22, X(tip[0]) + 3, Z(tip[1]) + 22, fill=ER, outline="")
        sv.create_text(12, 14, anchor="w", text=f"Fz = {r.F:.1f} N   écart vitre = {r.gap:.0f} mm", fill=INK)
        # vitre : cellules traitées
        N, c = 50, 300 / 50
        for (i, j), g in r.cells.items():
            col = "#7ac4d4" if g == "wet" else "#8fd4ac"
            if (i, j) not in E["cases"]:
                E["cases"][(i, j)] = fv.create_rectangle(i * c, j * c, (i + 1) * c, (j + 1) * c, fill=col, outline="")
            else:
                fv.itemconfig(E["cases"][(i, j)], fill=col)
        fv.delete("outil")
        h = r.W * .03
        fv.create_rectangle((r.y - r.wt / 2 + r.W / 2) / r.W * 300, (r.W / 2 - r.z - h) / r.W * 300,
                            (r.y + r.wt / 2 + r.W / 2) / r.W * 300, (r.W / 2 - r.z + h) / r.W * 300,
                            outline=ER, width=2, tags="outil")
        # courbe d'effort
        ch.delete("all")
        Y = lambda f: 150 - f * 8  # noqa: E731
        ch.create_rectangle(0, Y(15), 520, Y(10), fill="#dcefe4", outline="")
        ch.create_line(0, Y(12.5), 520, Y(12.5), dash=(4, 4), fill="#587176")
        ch.create_line(0, Y(15), 520, Y(15), fill=ER)
        pts = [c_ for i, f in enumerate(r.hist) for c_ in (i * 520 / 600, Y(min(f, 18)))]
        if len(pts) >= 4:
            ch.create_line(*pts, fill=AC, width=2)

    def tick():
        r = E["robot"]
        maintenant = time.perf_counter()
        dt, last[0] = maintenant - last[0], maintenant
        if not E["pause"] and r.st != "STO":
            last[1] = min(last[1] + dt * float(vit.get()), 3.0)
            while last[1] >= DT:
                r.step()
                last[1] -= DT
        if r.st == "IDLE":
            r.y, r.z = float(jy.get()), float(jz.get())
        dessiner(r)
        tele.config(text=r.telemetrie())
        msg.config(text=r.msg)
        badge.config(text=NOMS[r.st], bg=ER if r.st == "STO" else AC)
        if E["liaison"]:
            try:
                E["liaison"].envoyer(r)
            except Exception:  # noqa: BLE001
                E["liaison"] = None
        if r.st != E.get("prev"):
            if api and r.st in ("DONE", "STO"):
                import threading
                threading.Thread(target=envoyer_api, args=(api, r.rapport()), daemon=True).start()
            E["prev"] = r.st
        root.after(40, tick)

    tick()
    root.mainloop()
    if E["liaison"]:
        E["liaison"].fermer()


def lancer_headless(proto, api=None):
    r = Robot(proto)
    r.demarrer()
    fmax, trace = 0.0, None
    while r.st not in ("DONE", "STO") and r.t < 600:
        r.step()
        fmax = max(fmax, r.F)
        if r.st != trace:
            trace = r.st
            print(f"t={r.t:7.2f} s  {NOMS[r.st]:<32} Fz={r.F:5.2f} N")
    print(f"\nFin : {NOMS[r.st]} en {r.t:.1f} s — Fz max = {fmax:.2f} N — "
          f"eau tampon = {r.res.v * 1000:.0f} mL — surface traitée : {len(r.cells)} cellules")
    if api:
        envoyer_api(api, r.rapport())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="RoboClean Arm-V1 — poste de commande")
    ap.add_argument("--proto", action="store_true", help="échelle prototype 1:2,5")
    ap.add_argument("--port", help="port série de l'Arduino (ex. COM3, /dev/ttyUSB0)")
    ap.add_argument("--api", help="serveur de gestion, ex. http://localhost:3000")
    ap.add_argument("--headless", action="store_true", help="simule un cycle sans interface")
    a = ap.parse_args()
    lancer_headless(a.proto, a.api) if a.headless else lancer_gui(a.proto, a.port, a.api)

# --------------------------------------------------------------------------
# Sketch Arduino Uno + PCA9685 (à téléverser pour piloter le prototype réel)
#
# #include <Wire.h>
# #include <Adafruit_PWMServoDriver.h>
# Adafruit_PWMServoDriver pwm;
# const int PUMP = 7, ESTOP = 2;            // relais pompe, arrêt d'urgence NC
# float cur[6] = {90,90,90,90,90,90}, tgt[6] = {90,90,90,90,90,90};
# void setup() { Serial.begin(115200); pwm.begin(); pwm.setPWMFreq(50);
#                pinMode(PUMP, OUTPUT); pinMode(ESTOP, INPUT_PULLUP); }
# void loop() {
#   if (Serial.available()) { char c = Serial.read();
#     if (c == 'Q') for (int i = 0; i < 6; i++) tgt[i] = constrain(Serial.parseFloat(), 0, 180);
#     else if (c == 'P') digitalWrite(PUMP, Serial.parseInt()); }
#   if (digitalRead(ESTOP) == HIGH || analogRead(A0) >= 930)  // contact ouvert ou ~15 N
#     for (int i = 0; i < 6; i++) tgt[i] = cur[i];
#   for (int i = 0; i < 6; i++) {            // rampe 0,5° / 5 ms
#     cur[i] += constrain(tgt[i] - cur[i], -0.5, 0.5);
#     pwm.setPWM(i, 0, 205 + cur[i] * 205.0 / 180); }
#   delay(5); }
# --------------------------------------------------------------------------
