"""Utilities for defining a depot layout as a packing problem and solving it.
"""
import math
from decimal import Decimal
from random import randint
from operator import attrgetter
import itertools
from copy import copy
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.animation import FuncAnimation

language = "de"  # language for plots and animation, 'en' or 'de'


# Definition of constant depot layout parameters. Unit is metres unless
# otherwise stated.


# Parameters for SB (12m bus)
WIDTH = 2.55
LENGTH = 12

WIDTH_SAFE = 3.55  # n
LENGTH_SAFE = 12.5  # m
ANGLE_DIRECT = 45  # degrees (int between 0 and 75)

DIRECT_DISTANCE_A = 8
DIRECT_DISTANCE_B = 0

LINE_DISTANCE_A = 0
LINE_DISTANCE_B = 19.25

EDGE_DISTANCE_A = 8
EDGE_DISTANCE_B = 15


# Parameters for AB (18m bus)

# WIDTH = 2.55
# LENGTH = 18
#
# WIDTH_SAFE = 3.55   # n
# LENGTH_SAFE = 18.5  # m
# ANGLE_DIRECT = 45   # degrees (int between 0 and 75)
#
# DIRECT_DISTANCE_A = 10
# DIRECT_DISTANCE_B = 0
#
# LINE_DISTANCE_A = 0
# LINE_DISTANCE_B = 19.25
#
# EDGE_DISTANCE_A = 8
# EDGE_DISTANCE_B = 15


class ValidationError(Exception):
    def __init__(self, message):
        self.message = message


class Rectangle:
    """Rectangle representation as basis for subclasses.

    a and b: width and height
    x and y: coordinates of bottom left corner
    """

    def __init__(
        self,
        a,
        b,
        angle=0,
        x=0,
        y=0,
        fill=True,
        color="black",
        alpha=1.0,
        linestyle="-",
        **kwargs
    ):
        self.a = Decimal(str(a))
        self.b = Decimal(str(b))
        self.angle = int(angle)
        self._x = Decimal(str(x))
        self._y = Decimal(str(y))

        self.fill = fill
        self.color = color
        self.alpha = alpha
        self.linestyle = linestyle

    def __repr__(self):
        return "{%s} a=%s, b=%s, x=%s, y=%s, A=%s" % (
            type(self).__name__,
            self.a,
            self.b,
            self.x,
            self.y,
            self.A,
        )

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        self._x = Decimal(str(value))

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        self._y = Decimal(str(value))

    @property
    def A(self):
        return self.a * self.b

    @property
    def x_left(self):
        """Return x of the left side. Must be unrotated."""
        return self.x

    @property
    def x_right(self):
        """Return x of the right side. Must be unrotated."""
        return self.x + self.a

    @property
    def y_top(self):
        """Return y of the top side. Must be unrotated."""
        return self.y + self.b

    @property
    def y_bottom(self):
        """Return y of the bottom side. Must be unrotated."""
        return self.y

    @property
    def x_tr(self):
        """Return x of the top right corner. Must be unrotated."""
        return self.x + self.a

    @property
    def y_tr(self):
        """Return y of the top right corner. Must be unrotated."""
        return self.y + self.b

    @property
    def x_tl(self):
        """Return x of the top left corner. Must be unrotated."""
        return self.x

    @property
    def y_tl(self):
        """Return y of the top left corner. Must be unrotated."""
        return self.y + self.b

    @property
    def x_br(self):
        """Return x of the bottom right corner. Must be unrotated."""
        return self.x + self.a

    @property
    def y_br(self):
        """Return y of the bottom right corner. Must be unrotated."""
        return self.y

    @property
    def x_center(self):
        """Return x of the center. Must be unrotated."""
        return self.x + self.a / 2

    @property
    def y_center(self):
        """Return y of the center. Must be unrotated."""
        return self.y + self.b / 2


def fits_into(r1, r2):
    """Return True if rectangle r1 is smaller or equal than rectangle r2 in
    both dimensions.
    """
    return r1.a <= r2.a and r1.b <= r2.b


def xintersect(r1, r2):
    """Return True if unrotated rectangles r1 and r2 intersect in x direction.
    Same value is not considered intersection.
    """
    return r1.x_left < r2.x_right and r1.x_right > r2.x_left


def yintersect(r1, r2):
    """Return True if unrotated rectangles r1 and r2 intersect in y direction.
    Same value is not considered intersection.
    """
    return r1.y_bottom < r2.y_top and r1.y_top > r2.y_bottom


def intersect(r1, r2):
    """Return True if unrotated rectangles r1 and r2 intersect. Same value is
    not considered intersection.
    """
    return xintersect(r1, r2) and yintersect(r1, r2)


def contains(r1, r2):
    """Return True if rectangle r1 completely encloses rectangle r2. Must be
    unrotated.
    """
    return all(
        [
            r1.x_left <= r2.x_left,
            r1.y_bottom <= r2.y_bottom,
            r1.x_right >= r2.x_right,
            r1.y_top >= r2.y_top,
        ]
    )


def contains_point(r, p):
    """Return True if rectangle r contains point p. Must be unrotated."""
    return all([r.x_left <= p.x <= r.x_right, r.y_bottom <= p.y <= r.y_top])


class RectangleWithInner(Rectangle):
    """Rectangle containing two or more identical rectangles.
    Inner rectangles are stacked in the direction of increasing y.

    m, n: [int or float] a, b of the inner rectangles
    """

    def __init__(self, m, n, count_inner, x=0, y=0, **kwargs):
        if not str(count_inner).isdigit() or count_inner < 2:
            raise ValueError("count_inner must be a natural number > 1.")
        self.count_inner = count_inner
        self.inner = Rectangle(m, n, 0, x, y, color="cornflowerblue")

        super(RectangleWithInner, self).__init__(a=m, b=count_inner * n, x=x, y=y)

        self.fill = False

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        value = Decimal(str(value))
        self.inner.x = value
        self._x = value

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        value = Decimal(str(value))
        self.inner.y = value
        self._y = value

    @property
    def A_inner(self):
        """Return the total space of inner rectangles."""
        return self.inner.A * self.count_inner

    @property
    def util_rate(self):
        return self.A_inner / self.A

    def x_inner(self, *args):
        """Return the x coordinate of inner rectangles (same for all)."""
        return self.inner.x

    def y_inner(self, index):
        """Return the y coordinate of the inner rectangle with *index*
        (starting at 0).
        """
        return self.inner.y + self.inner.b * index


class RectangleWithRotatableInner(Rectangle):
    """Rectangle containing one or more identical rotatable rectangles.
    Inner rectangles are stacked in the direction of increasing y.

    Parameters:
    m, n: [int or float] a, b of the inner rectangles

    Attributes:
    inner: [Rectangle] bottom inner rectangle

    """

    def __init__(self, m, n, count_inner=1, angle_inner=45, x=0, y=0, **kwargs):
        self.h = Decimal(str(0))
        self.inner = Rectangle(m, n, angle_inner, x, y, color="orange")
        if not str(count_inner).isdigit() or count_inner < 1:
            raise ValueError("'count_inner' must be a natural number > 0.")
        self.count_inner = count_inner

        super(RectangleWithRotatableInner, self).__init__(a=0, b=0, x=x, y=y)

        self.rotate_inner(angle_inner)
        self.fill = False

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        value = Decimal(str(value))
        # Move inner x by difference because its not equal to self.x
        self.inner.x += value - self._x
        self._x = value

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        value = Decimal(str(value))
        self.inner.y += value - self._y
        self._y = value

    @property
    def A_inner(self):
        """Return the total space of inner rectangles."""
        return self.inner.A * self.count_inner

    @property
    def util_rate(self):
        return self.A_inner / self.A

    def rotate_inner(self, angle):
        """Set the rotation of the inner rectangles and update the parameters
        of self.

        A reference drawing for names used in this method such as m, AQ is
        located at depot_layout_opt/doc/direct_details.pdf.

        angle: [int or float] value between -75 and 75
        """
        if not str(abs(angle)).isdigit() or not 0 <= abs(angle) <= 75:
            raise ValueError("angle_inner must be between -75 " "and 75.")
        angle_rad = math.radians(angle)
        m = self.inner.a
        n = self.inner.b
        PA = n * Decimal(str(math.sin(angle_rad)))  # line PA in drawing
        AQ = m * Decimal(str(math.cos(angle_rad)))  # line AQ in drawing

        self.h = n / Decimal(str(math.cos(angle_rad)))
        PD = n * Decimal(str(math.cos(angle_rad)))  # line PD in drawing
        DS = m * Decimal(str(math.sin(angle_rad)))  # line DS in drawing

        self.inner.angle = angle

        if angle < 0:
            self.a = -PA + AQ
            self.b = PD - DS + (self.count_inner - 1) * self.h
            self.inner.x = self.x
            self.inner.y = self.y - DS
        else:
            self.a = PA + AQ
            self.b = PD + DS + (self.count_inner - 1) * self.h
            self.inner.x = self.x + PA

    def x_inner(self, *args):
        """Return the x coordinate of inner rectangles (same for all)."""
        return self.inner.x

    def y_inner(self, index):
        """Return the y coordinate of the inner rectangle with *index*
        (starting at 0).
        """
        return self.inner.y + index * self.h


class RectangleWithRotatedDoubleRowInner(Rectangle):
    """Rectangle containing two or more identical sized rectangles that
    are rotated by 45° and arranged in a double row.

    Parameters:
    m, n: [int or float] a, b of the inner rectangles

    Attributes:
    inner: [Rectangle] bottom inner rectangle

    """

    def __init__(self, m, n, count_inner=2, x=0, y=0, **kwargs):
        self.h = Decimal(str(0))
        self.inner_left = Rectangle(m, n, 45, x, y, color="orange")
        self.inner_right = Rectangle(m, n, 135, x, y, color="orange")
        if not str(count_inner).isdigit() or count_inner < 2:
            raise ValueError("'count_inner' must be a natural number > 1.")
        self.count_inner = count_inner

        super(RectangleWithRotatedDoubleRowInner, self).__init__(a=0, b=0, x=x, y=y)
        self.fill = False

        # Update values from rotating the inner rectangles to 45° for left
        # and 135° for right.
        # A reference drawing for names used below such as m, PA ism located at
        # depot_layout_opt/doc/direct_details.pdf.

        uneven_count = count_inner % 2
        angle_rad = math.radians(45)
        m = self.inner_left.a  # reassign due to conversion to decimal
        n = self.inner_left.b

        PA = n * Decimal(str(math.sin(angle_rad)))  # line PD in drawing
        ABx = m * Decimal(str(math.cos(angle_rad)))  # x value of line AQ in drawing
        self.a = PA + 2 * ABx
        self.h = n / Decimal(str(math.cos(angle_rad)))
        PD = n * Decimal(str(math.cos(angle_rad)))  # line PD in drawing
        DS = m * Decimal(str(math.sin(angle_rad)))  # line DS in drawing
        n_slots_left = Decimal(str(self.count_inner / 2)) + int(
            not uneven_count
        ) * Decimal(str(0.5))
        self.b = PD + DS + (n_slots_left - 1) * self.h + uneven_count * self.h / 2

        self.inner_left.x = self.x + PA
        self.inner_right.x = self.x + self.a
        self.inner_right.y = y + self.h

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        value = Decimal(str(value))
        # Move inner x by difference because its not equal to self.x
        self.inner_left.x += value - self._x
        self.inner_right.x += value - self._x
        self._x = value

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        value = Decimal(str(value))
        self.inner_left.y = value
        # Move inner_right y by difference because its not equal to self.x
        self.inner_right.y += value - self._y
        self._y = value

    @property
    def A_inner(self):
        """Return the total space of inner rectangles."""
        return self.inner_left.A * self.count_inner

    @property
    def util_rate(self):
        return self.A_inner / self.A

    def x_inner(self, index):
        """Return the x coordinate of inner rectangle with *index*. Indexing
        starts at zero, alternating left and right from the bottom left towards
        the top.
        """
        if index % 2:
            return self.inner_right.x
        else:
            return self.inner_left.x

    def y_inner(self, index):
        """Return the y coordinate of inner rectangle with *index*. Indexing
        starts at zero, alternating left and right from the bottom left towards
        the top.
        """
        if index % 2:
            return self.inner_right.y + Decimal(str((index - 1) / 2)) * self.h
        else:
            return self.inner_left.y + Decimal(str(index / 2)) * self.h


class Available(Rectangle):
    """Rectangle with default parameters to represent available space in
    a bin.
    """

    def __init__(
        self, a, b, x=0, y=0, fill=False, color="black", alpha=0.7, linestyle="--"
    ):
        super(Available, self).__init__(a, b, 0, x, y, fill, color, alpha, linestyle)


def alab_arib(av, item):
    """Return a new [Available] spanning from (av.x_left, av.y_bottom) to
    (av.x_right, item.y_bottom).
    """
    return Available(
        a=av.x_right - av.x_left,
        b=item.y_bottom - av.y_bottom,
        x=av.x_left,
        y=av.y_bottom,
    )


def alit_arat(av, item):
    """Return a new [Available] spanning from (av.x_left, item.y_top) to
    (av.x_right, av.y_top).
    """
    return Available(
        a=av.x_right - av.x_left, b=av.y_top - item.y_top, x=av.x_left, y=item.y_top
    )


def alab_ilat(av, item):
    """Return a new [Available] spanning from (av.x_left, av.y_bottom) to
    (item.x_left, av.y_top).
    """
    return Available(
        a=item.x_left - av.x_left, b=av.y_top - av.y_bottom, x=av.x_left, y=av.y_bottom
    )


def irab_arat(av, item):
    """Return a new [Available] spanning from (item.x_right, av.y_bottom) to
    (av.x_right, av.y_top).
    """
    return Available(
        a=av.x_right - item.x_right,
        b=av.y_top - av.y_bottom,
        x=item.x_right,
        y=av.y_bottom,
    )


class Bin(Rectangle):
    """Rectangular container with best-fit-decreasing algorithm for rectangle
    packing.
    """

    def __init__(self, a, b, record_history=True, fill=False):
        super(Bin, self).__init__(a, b, fill=fill)
        self.items = []
        self.availables = []
        self.packed_items = []
        self.record_history = record_history
        self.history = {"items": [], "availables": []}
        self._feasible = None
        self._precheck_passed = None

        self.fig = None
        self.ax = None

        self.availables.append(Available(a, b, x=0, y=0))

    @property
    def valid(self):
        """Return True if none of the rectangles in self.packed_items
        intersect, else return False.
        """
        for combo in itertools.combinations(self.packed_items, 2):
            if intersect(*combo):
                return False
        return True

    @property
    def feasible(self):
        """Return the result of the packing attempt as boolean. None until
        self.pack is called.
        """
        return self._feasible

    @property
    def count_inner(self):
        """Return the total number of inner rectangles of items."""
        return sum(
            pitem.count_inner for pitem in self.items if hasattr(pitem, "count_inner")
        )

    @property
    def A_inner(self):
        """Return the total space of items in unpacked state. Excludes space
        for distances (if applicable).
        """
        return sum(item.A for item in self.items)

    @property
    def util_rate(self):
        """Percentage of the total bin space occupied by packed items."""
        used = sum(pitem.A for pitem in self.packed_items)
        return used / self.A

    @property
    def precheck_passed(self):
        """Return the result of the precheck as boolean. None until
        self.precheck is called.
        """
        return self._precheck_passed

    def precheck(self):
        """Quick checks applicable before packing to detect if items fit."""
        for item in self.items:
            if item.a > self.a:
                # print('Side a of an item is too large.')
                self._precheck_passed = False
                return
            if item.b > self.b:
                # print('Side b of an item is too large.')
                self._precheck_passed = False
                return
        if sum(item.A for item in self.items) > self.A:
            # print('The sum of areas of items is larger than the available '
            #       'area.')
            self._precheck_passed = False
            return

        import itertools

        combos = itertools.combinations(self.items, 2)
        for i1, i2 in combos:
            assert i1 is not i2

        self._precheck_passed = True

    def pack(self):
        """Try to distribute all items into self so that none intersect and
        return True if successful, else return False.
        """
        if self.packed_items:
            raise RuntimeError("Cannot pack again without reset.")
        if not self.items:
            raise RuntimeError("Cannot pack because item list is emtpy.")

        self.precheck()
        if not self.precheck_passed:
            self._feasible = False
            return

        # Simplified sorting: Sort by 'a' (primary), then by 'b'.
        # 'conflict_category' is not necessary in the depot scenario as long as
        # 'a' yields the same result.
        self.items.sort(key=attrgetter("conflict_category", "a", "b"), reverse=True)
        # items.sort(key=attrgetter('conflict_category', 'a', 'b'), reverse=True)

        if self.record_history:
            self.history["items"].append(self.packed_items.copy())
            self.history["availables"].append(self.availables.copy())

        for item in self.items:
            # print('Next item: %s' % item)
            if not self.put(item):
                self._feasible = False
                return

            if self.record_history:
                self.history["items"].append(self.packed_items.copy())
                self.history["availables"].append(self.availables.copy())

        assert self.valid
        self._feasible = True
        # print('packed')

    def put(self, item):
        """Call try_put to determine if item fits into self. If successful,
        execute the action and return True, else False.
        """
        success, av_put = self.try_put(item)

        if success:
            self.packed_items.append(item)
            self.update_availables(item)

        return success

    def try_put(self, item):
        """Determine if there is valid available space for item. Return (True,
        the available space rectangle) if yes, else (False, None). x and y of
        item may be manipulated regardless of the
        result.
        """
        for av in self.availables:
            if av.a >= item.a and av.b >= item.b:
                # success
                item.x = av.x
                item.y = av.y
                return True, av
        return False, None

    def update_availables(self, item):
        """Update availables after packing *item*."""
        # Split existing availables that intersect item into several smaller
        # ones depending on the relative location
        new_avs = []
        removals = []
        for av in self.availables:
            if intersect(av, item):
                removals.append(av)
                # Get differences of bottom left and top right corner
                # coordinates
                dx_bl = av.x_left - item.x_left
                dy_bl = av.y_bottom - item.y_bottom
                dx_tr = av.x_right - item.x_right
                dy_tr = av.y_top - item.y_top

                # Simplify differences to booleans
                bx_bl = dx_bl >= 0
                by_bl = dy_bl >= 0
                bx_tr = dx_tr > 0
                by_tr = dy_tr > 0

                if bx_bl:
                    if by_bl:
                        if bx_tr:
                            if by_tr:
                                new_avs.extend(self.case_8(av, item))
                            else:
                                new_avs.extend(self.case_12(av, item))
                        else:
                            if by_tr:
                                new_avs.extend(self.case_11(av, item))
                            else:
                                new_avs.extend(self.case_15(av, item))
                    else:
                        if bx_tr:
                            if by_tr:
                                new_avs.extend(self.case_1(av, item))
                            else:
                                new_avs.extend(self.case_2(av, item))
                        else:
                            if by_tr:
                                new_avs.extend(self.case_14(av, item))
                            else:
                                new_avs.extend(self.case_9(av, item))
                else:
                    if by_bl:
                        if bx_tr:
                            if by_tr:
                                new_avs.extend(self.case_7(av, item))
                            else:
                                new_avs.extend(self.case_13(av, item))
                        else:
                            if by_tr:
                                new_avs.extend(self.case_6(av, item))
                            else:
                                new_avs.extend(self.case_10(av, item))
                    else:
                        if bx_tr:
                            if by_tr:
                                new_avs.extend(self.case_16(av, item))
                            else:
                                new_avs.extend(self.case_3(av, item))
                        else:
                            if by_tr:
                                new_avs.extend(self.case_5(av, item))
                            else:
                                new_avs.extend(self.case_4(av, item))

        for av in removals:
            self.availables.remove(av)
        self.availables.extend(new_avs)

        # Remove availables that are fully enclosed by others and therefore
        # redundant
        removals = set()
        # Sort to be able to use combinations instead of permutations (less
        # pairs, therefore faster)
        pool = sorted(self.availables, key=lambda av: av.A, reverse=True)
        for r1, r2 in itertools.combinations(pool, 2):
            if contains(r1, r2):
                removals.add(r2)
                # print('Removing %s contained by %s' % (r1, r2))
        for av in removals:
            self.availables.remove(av)

        # Validation
        for av in self.availables:
            assert av.a > 0 and av.b > 0
            for pitem in self.packed_items:
                assert not intersect(av, pitem)
                # if intersect(av, pitem):
                #     raise ValidationError('Intersection of av: %s and item %s' % (av, pitem))

        self.availables.sort(key=attrgetter("a"))
        self.availables.sort(key=attrgetter("b"))

    @staticmethod
    def case_1(av, item):
        """Case 1 of splitting av into smaller rectangles."""
        return [alab_arib(av, item), alit_arat(av, item), irab_arat(av, item)]

    @staticmethod
    def case_2(av, item):
        return [alab_arib(av, item), irab_arat(av, item)]

    @staticmethod
    def case_3(av, item):
        return [alab_arib(av, item), alab_ilat(av, item), irab_arat(av, item)]

    @staticmethod
    def case_4(av, item):
        return [alab_arib(av, item), alab_ilat(av, item)]

    @staticmethod
    def case_5(av, item):
        return [alab_arib(av, item), alit_arat(av, item), alab_ilat(av, item)]

    @staticmethod
    def case_6(av, item):
        return [alit_arat(av, item), alab_ilat(av, item)]

    @staticmethod
    def case_7(av, item):
        return [alit_arat(av, item), alab_ilat(av, item), irab_arat(av, item)]

    @staticmethod
    def case_8(av, item):
        return [alit_arat(av, item), irab_arat(av, item)]

    @staticmethod
    def case_9(av, item):
        return [alab_arib(av, item)]

    @staticmethod
    def case_10(av, item):
        return [alab_ilat(av, item)]

    @staticmethod
    def case_11(av, item):
        return [alit_arat(av, item)]

    @staticmethod
    def case_12(av, item):
        return [irab_arat(av, item)]

    @staticmethod
    def case_13(av, item):
        return [alab_ilat(av, item), irab_arat(av, item)]

    @staticmethod
    def case_14(av, item):
        return [alab_arib(av, item), alit_arat(av, item)]

    @staticmethod
    def case_15(av, item):
        raise ValidationError("case 15 (ex)")

    @staticmethod
    def case_16(av, item):
        raise ValidationError("case 16 (bo)")

    def repack(self):
        """Reset packing values and pack again."""
        self.availables = []
        self.packed_items = []
        self.history = {"items": [], "availables": []}
        self._feasible = None
        self._precheck_passed = None

        self.fig = None
        self.ax = None

        self.availables.append(Rectangle(self.a, self.b, x=0, y=0))

        self.pack()

    def draw(self):
        """Plot self.packed_items."""
        if not self.packed_items:
            print("No packed items to draw.")
        return draw_rectangles_newplot(
            self.packed_items + [self], (0, float(self.a)), (0, float(self.b)), False
        )

    def save_drawing(
        self, filename, formats=("pdf",), confirm=True, dpi=None, show=False, **kwargs
    ):
        """Save what self.draw shows.

        filename: [str] including path, excluding extension. Existing files
            with the same name are overwritten without confirmation prompt.
        formats: [tuple] of file extensions [str]
        dpi: Parameter of plt.savefig()
        show: [bool] if False, the fig is closed automatically
        """
        fig, ax = self.draw()
        if "png" in formats:
            fig.savefig(filename + ".png", dpi=dpi, **kwargs)
            if confirm:
                print("Saved %s.png" % filename)
        if "pdf" in formats:
            fig.savefig(filename + ".pdf", dpi=dpi, **kwargs)
            if confirm:
                print("Saved %s.pdf" % filename)

        if not show:
            plt.close(fig)

    def animate(self):
        anim = self._animate(draw_distances=False)
        return anim

    def _animate(self, draw_distances):
        """Based on https://stackoverflow.com/a/49382421"""
        if not self.record_history or not self.history:
            print(
                "animate requires record_history to be be True and at least "
                "one entry in history."
            )
            return None

        def get_sub():
            """Generator to get the next in availables of the current frame."""
            imax = len(anim.frame_seq_subs[anim.i]) - 1
            anim.i_sub = 0
            yield anim.frame_seq_subs[anim.i][anim.i_sub]
            while True:
                anim.i_sub += anim.direction_sub
                if anim.i_sub < 0:
                    anim.i_sub = imax
                elif anim.i_sub > imax:
                    anim.i_sub = 0
                yield anim.frame_seq_subs[anim.i][anim.i_sub]

        def get_specific(i):
            recs = anim.frame_seq_items[i].copy()
            subs = anim.frame_seq_subs[i].copy()
            recs.extend(subs)
            return recs

        def get_next():
            """Generator to determine the frame and get its next main
            rectangles and all availables.
            """
            imax = len(anim.frame_seq_items) - 1
            anim.i = 0
            plt.title(tl("Step") + " " + str(anim.i))
            anim.subgen = get_sub()

            yield get_specific(anim.i)

            while True:
                anim.i += anim.direction
                if anim.i < 0:
                    anim.i = imax
                    plt.gca().texts.clear()
                elif anim.i > imax:
                    anim.i = 0
                    plt.gca().texts.clear()
                anim.subgen = get_sub()
                plt.title(tl("Step") + " " + str(anim.i))

                yield get_specific(anim.i)

        def on_press(event):
            """Action to execute on buttom press."""
            if event.key.isspace():
                if anim.running:
                    anim.event_source.stop()
                else:
                    anim.event_source.start()
                anim.running ^= True  # toggle through xor
            elif event.key == "left":
                anim.direction = -1
            elif event.key == "right":
                anim.direction = +1
            elif event.key == "up":
                anim.direction_sub = +1
            elif event.key == "down":
                anim.direction_sub = -1

            # Manually update the plot
            if event.key in ["left", "right"]:
                plt.gca().texts.clear()
                rectangles = next(anim.frame_seq)
                draw_rectangles(rectangles, draw_distances)

                # plt.title('')
                # from eflips.depot.evaluation import savefig
                # def save(fig, filename):
                #     savefig(fig, filename, formats=('png',), dpi=300)

                # save(plt.gcf(), tl('Step') + ' ' + str(anim.i) + ', _')

            if not anim.running and event.key in ["up", "down"]:
                plt.gca().texts.clear()
                rectangles = anim.frame_seq_items[anim.i].copy()
                av = copy(next(anim.subgen))
                av.fill = True
                av.color = "green"
                av.alpha = 0.5
                rectangles.append(av)
                rectangles.extend(anim.frame_seq_subs[anim.i])
                title = (
                    tl("Step")
                    + " "
                    + str(anim.i)
                    + ", "
                    + tl("av")
                    + " "
                    + tl("no")
                    + " "
                    + str(anim.i_sub + 1)
                )
                plt.title(title)
                draw_rectangles(rectangles, draw_distances)

                # plt.title('')
                # from eflips.depot.evaluation import savefig
                # def save(fig, filename):
                #     savefig(fig, filename, formats=('png',), dpi=300)

                # save(plt.gcf(), title)

        fig, ax = plt.subplots()
        fig.canvas.mpl_connect("key_press_event", on_press)
        anim = FuncAnimation(
            fig,
            draw_rectangles,
            frames=get_next,
            fargs=(draw_distances,),
            interval=1000,
            repeat=True,
            save_count=len(self.history["items"]),
        )
        anim.running = True
        anim.direction = +1
        anim.i = 0
        anim.i_sub = 0

        anim.frame_seq_items = self.history["items"].copy()
        # Add self to item history to also draw self (and the bin edge
        # distances for BinWithDistances) without manipulating the history.
        for entry in anim.frame_seq_items:
            entry.append(self)

        anim.frame_seq_subs = self.history["availables"]
        anim.subgen = None
        anim.direction_sub = +1

        ax.set_aspect("equal")
        plt.xlim(0, float(self.a))
        plt.ylim(0, float(self.b))

        dpi = fig.get_dpi()
        fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))

        plt.show()

        return anim

    def save_animation(self, filename, confirm=True, fps=1.5, bitrate=1800, **kwargs):
        """Save what self.animate shows as .mp4.

        filename: [str] including path, excluding extension. Existing files
            with the same name are overwritten without confirmation prompt.

        Accepts arguments for ffmpeg writer init and FuncAnimation.save as
        kwargs.
        """
        # Set up formatting for the movie files
        Writer = matplotlib.animation.writers["ffmpeg"]
        writer = Writer(fps=fps, metadata=dict(artist="Me"), bitrate=bitrate, **kwargs)

        anim = self.animate()
        anim.save(filename + ".mp4", writer=writer, **kwargs)
        if confirm:
            print("Saved %s.mp4" % filename)


class DistanceRectangle(Rectangle):
    """Rectangle with default parameters to represent a buffer distance in
    packing with distances.
    """

    def __init__(
        self, a, b, x=0, y=0, fill=True, color="grey", alpha=0.5, linestyle=":"
    ):
        super(DistanceRectangle, self).__init__(
            a, b, 0, x, y, fill, color, alpha, linestyle
        )


class BinWithDistances(Bin):
    """Rectangular container with best-fit-decreasing-algorithm for rectangle
    packing with optional buffer distances between items as well as between
    items and the container edges.

    Distance handling based on https://doi.org/10.1051/ro/2012007
    """

    def __init__(self, a, b, record_history=True):
        super(BinWithDistances, self).__init__(a, b, record_history)

        self.distance_left_inner = DistanceRectangle(EDGE_DISTANCE_A, b, x=0, y=0)
        self.distance_bottom_inner = DistanceRectangle(a, EDGE_DISTANCE_B, x=0, y=0)
        self.distance_right_inner = DistanceRectangle(
            EDGE_DISTANCE_A, b, x=self.x_right - Decimal(str(EDGE_DISTANCE_A)), y=0
        )
        self.distance_top_inner = DistanceRectangle(
            a, EDGE_DISTANCE_B, x=0, y=self.y_top - Decimal(str(EDGE_DISTANCE_B))
        )

    def try_put(self, item):
        """Determine if there is valid available space with respect to buffer
        distances for item. Return (True, the Available rectangle) if
        yes, else (False, None). x and y of item may be manipulated regardless
        of the result.
        """
        for av in self.availables:
            if av.a >= item.a and av.b >= item.b:
                # print('Considering av %s' % av)
                # item fits without buffer distance. Assign preliminary position.
                item.x = av.x
                item.y = av.y

                # Left bin edge
                if item.distance_left.x_left < self.x_left or intersect(
                    item, self.distance_left_inner
                ):
                    oldx = item.x
                    distance = max(item.distance_left.a, self.distance_left_inner.a)
                    item.x = self.x_left + distance
                    assert oldx < item.x
                    if not contains(av, item):
                        continue

                # Bottom bin edge
                if item.distance_bottom.y_bottom < self.y_bottom or intersect(
                    item, self.distance_bottom_inner
                ):
                    oldy = item.y
                    distance = max(item.distance_bottom.b, self.distance_bottom_inner.b)
                    item.y = self.y_bottom + distance
                    assert oldy <= item.y
                    if not contains(av, item):
                        continue

                # Move item to the right and top until all buffer distance
                # conflicts on the left and bottom of *item* are resolved. The
                # validity is ignored here and checked later.
                left_ok = False
                bottom_ok = False
                i = -1
                invalid = False
                while not (left_ok and bottom_ok):
                    i += 1
                    if i > 50:
                        raise ValidationError("Feels like too many iterations")
                    # print(' ' * i + 'Checking left and right. Iteration=%s, item=%s. x=%s, y=%s' % (i, item.ID, item.x, item.y))

                    # Left
                    for pitem in self.packed_items:
                        if intersect(item.distance_left, pitem) or intersect(
                            item, pitem.distance_right
                        ):
                            oldx = item.x
                            distance = max(item.distance_left.a, pitem.distance_right.a)
                            item.x = pitem.x_right + distance

                            bottom_ok = False
                            assert oldx <= item.x
                            # print('--x Conflict with item %s. Moved %s to the right by %s. oldx: %s, newx: %s' % (pitem.ID, item.ID, distance, oldx, item.x))
                    if not contains(av, item):
                        invalid = True
                        break
                    left_ok = True

                    # Bottom
                    if not bottom_ok:
                        for pitem in self.packed_items:
                            if intersect(item.distance_bottom, pitem) or intersect(
                                item, pitem.distance_top
                            ):
                                oldy = item.y
                                distance = max(
                                    item.distance_bottom.b, pitem.distance_top.b
                                )
                                item.y = pitem.y_top + distance

                                left_ok = False
                                assert oldy <= item.y
                                # print(item)
                                # print(pitem)
                                # print(item.distance_bottom)
                                # print(pitem.distance_top)
                                # print('--y Conflict with item %s. Moved %s to the top by %s. pitem.y_top=%s, oldy: %f, newy: %s' % (pitem.ID, item.ID, distance, pitem.y_top, oldy, item.y))
                        if not contains(av, item):
                            invalid = True
                            break
                        bottom_ok = True

                if invalid:
                    continue

                # Checks that make this av invalid for item if True
                invalid = False

                # Intersection with other items
                for pitem in self.packed_items:
                    if intersect(item, pitem):
                        invalid = True
                        break
                if invalid:
                    continue

                # Right bin edge
                if item.distance_right.x_right > self.x_right or intersect(
                    item, self.distance_right_inner
                ):
                    # print('Bin conflict on the right side.')
                    continue

                # Right
                for pitem in self.packed_items:
                    if intersect(item.distance_right, pitem) or intersect(
                        item, pitem.distance_left
                    ):
                        # print('Item conflict on the right side.')
                        invalid = True
                        break
                if invalid:
                    continue

                # Top bin edge
                if item.distance_top.y_top > self.y_top or intersect(
                    item, self.distance_top_inner
                ):
                    # print('Bin conflict on the top side.')
                    continue

                # Top
                for pitem in self.packed_items:
                    if intersect(item.distance_top, pitem) or intersect(
                        item, pitem.distance_bottom
                    ):
                        # print('Item conflict on the top side.')
                        invalid = True
                        break
                if invalid:
                    continue

                # print('Suitable av found')
                return True, av
        # print('Couldnt find suitable av')
        return False, None

    @staticmethod
    def case_16(av, item):
        return [
            alab_arib(av, item),
            alit_arat(av, item),
            alab_ilat(av, item),
            irab_arat(av, item),
        ]

    def draw(self):
        """Plot self.packed_items and the distances."""
        if not self.packed_items:
            print("No packed items to draw.")
        return draw_rectangles_newplot(
            self.packed_items + [self], (0, float(self.a)), (0, float(self.b)), True
        )

    def animate(self):
        anim = self._animate(draw_distances=True)
        return anim


class DistanceLeft(DistanceRectangle):
    """Buffer rectangle on the outer left side of the *item* rectangle."""

    def __init__(self, a, item):
        super(DistanceLeft, self).__init__(a, 0)

        self.item = item

    @property
    def b(self):
        return self.item.b

    @b.setter
    def b(self, value):
        pass

    @property
    def x(self):
        return self.item.x - self.a

    @x.setter
    def x(self, value):
        raise RuntimeError("x of distance rectangle cannot be set directly.")

    @property
    def y(self):
        return self.item.y

    @y.setter
    def y(self, value):
        raise RuntimeError("y of distance rectangle cannot be set directly.")


class DistanceBottom(DistanceRectangle):
    """Buffer rectangle on the outer bottom side of the *item* rectangle."""

    def __init__(self, b, item):
        super(DistanceBottom, self).__init__(0, b)

        self.item = item

    @property
    def a(self):
        return self.item.a

    @a.setter
    def a(self, value):
        pass

    @property
    def x(self):
        return self.item.x

    @x.setter
    def x(self, value):
        raise RuntimeError("x of distance rectangle cannot be set directly.")

    @property
    def y(self):
        return self.item.y - self.b

    @y.setter
    def y(self, value):
        raise RuntimeError("y of distance rectangle cannot be set directly.")


class DistanceRight(DistanceRectangle):
    """Buffer rectangle on the outer right side of the *item* rectangle."""

    def __init__(self, a, item):
        super(DistanceRight, self).__init__(a, 0)

        self.item = item

    @property
    def b(self):
        return self.item.b

    @b.setter
    def b(self, value):
        pass

    @property
    def x(self):
        return self.item.x_br

    @x.setter
    def x(self, value):
        raise RuntimeError("x of distance rectangle cannot be set directly.")

    @property
    def y(self):
        return self.item.y_br

    @y.setter
    def y(self, value):
        raise RuntimeError("y of distance rectangle cannot be set directly.")


class DistanceTop(DistanceRectangle):
    """Buffer rectangle on the outer top side of the *item* rectangle."""

    def __init__(self, b, item):
        super(DistanceTop, self).__init__(0, b)

        self.item = item

    @property
    def a(self):
        return self.item.a

    @a.setter
    def a(self, value):
        pass

    @property
    def x(self):
        return self.item.x_tl

    @x.setter
    def x(self, value):
        raise RuntimeError("x of distance rectangle cannot be set directly.")

    @property
    def y(self):
        return self.item.y_tl

    @y.setter
    def y(self, value):
        raise RuntimeError("y of distance rectangle cannot be set directly.")


class RectangleWithDistances(Rectangle):
    """A rectangle that may have a buffer distance at each side. Without
    rotation.

    Parameters:
    d_l, d_b, d_r, d_t: [int or float] buffer distance at left, bottom, right
        and top sides.
    """

    def __init__(self, a=0, b=0, x=0, y=0, d_l=0, d_b=0, d_r=0, d_t=0, **kwargs):
        super(RectangleWithDistances, self).__init__(
            a=a, b=b, angle=0, x=x, y=y, **kwargs
        )

        self.distance_left = DistanceLeft(d_l, self)
        self.distance_bottom = DistanceBottom(d_b, self)
        self.distance_right = DistanceRight(d_r, self)
        self.distance_top = DistanceTop(d_t, self)

    @property
    def a_with_distances(self):
        return sum((self.distance_left.a, self.a, self.distance_right.a))

    @property
    def b_with_distances(self):
        return sum((self.distance_bottom.b, self.b, self.distance_top.b))

    @property
    def x_with_distances(self):
        """When in combination with y_with_distances: this point may intersect
        another shape.
        """
        return self.distance_left.x

    @property
    def y_with_distances(self):
        """When in combination with x_with_distances: this point may intersect
        another shape.
        """
        return self.distance_bottom.y

    @property
    def A_distances(self):
        """Return the area covered by distances."""
        return sum(
            (
                self.distance_left.A,
                self.distance_bottom.A,
                self.distance_right.A,
                self.distance_top.A,
            )
        )

    @property
    def A_with_distances(self):
        """Return the total area including distances."""
        return self.A + self.A_distances


class VisuDataLine(RectangleWithDistances, RectangleWithInner):
    """Representation of a Line area.

    text: [str] to draw in the center of the rectangle
    """

    conflict_category = 2

    def __init__(
        self,
        m=WIDTH_SAFE,
        n=LENGTH_SAFE,
        capacity=2,
        x=0,
        y=0,
        d_l=LINE_DISTANCE_A,
        d_b=LINE_DISTANCE_B,
        d_r=LINE_DISTANCE_A,
        d_t=LINE_DISTANCE_B,
        text="",
    ):
        super(VisuDataLine, self).__init__(
            m=m, n=n, count_inner=capacity, x=x, y=y, d_l=d_l, d_b=d_b, d_r=d_r, d_t=d_t
        )
        self.text = text

    @property
    def capacity(self):
        """Alias of self.count_inner."""
        return self.count_inner

    @property
    def util_rate_with_distances(self):
        return self.inner.A * self.count_inner / self.A_with_distances


class VisuDataDirectSingleRow(RectangleWithDistances, RectangleWithRotatableInner):
    """Representation of a single row direct area.

    angle_inner: makes sense until max. 75 degrees (with 12.5x3.5 slots)
    text: [str] to draw in the center of the rectangle
    """

    conflict_category = 3

    def __init__(
        self,
        m=LENGTH_SAFE,
        n=WIDTH_SAFE,
        angle_inner=45,
        capacity=1,
        x=0,
        y=0,
        d_l=DIRECT_DISTANCE_A,
        d_b=DIRECT_DISTANCE_B,
        d_r=0,
        d_t=DIRECT_DISTANCE_B,
        text="",
    ):
        super(VisuDataDirectSingleRow, self).__init__(
            m=m,
            n=n,
            count_inner=capacity,
            angle_inner=angle_inner,
            x=x,
            y=y,
            d_l=d_l,
            d_b=d_b,
            d_r=d_r,
            d_t=d_t,
        )
        self.text = text

    @property
    def capacity(self):
        """Alias of self.count_inner."""
        return self.count_inner

    @property
    def util_rate_with_distances(self):
        return self.inner.A * self.count_inner / self.A_with_distances


class VisuDataDirectSingleRow_90(RectangleWithDistances, RectangleWithRotatableInner):
    """Representation of a single row direct area.

    angle_inner: makes sense until max. 75 degrees (with 12.5x3.5 slots)
    text: [str] to draw in the center of the rectangle
    """

    conflict_category = 1

    def __init__(
        self,
        m=LENGTH_SAFE,
        n=WIDTH_SAFE,
        angle_inner=-45,
        capacity=1,
        x=0,
        y=0,
        d_l=0,
        d_b=DIRECT_DISTANCE_B,
        d_r=DIRECT_DISTANCE_A,
        d_t=DIRECT_DISTANCE_B,
        text="",
    ):
        super(VisuDataDirectSingleRow_90, self).__init__(
            m=m,
            n=n,
            count_inner=capacity,
            angle_inner=angle_inner,
            x=x,
            y=y,
            d_l=d_l,
            d_b=d_b,
            d_r=d_r,
            d_t=d_t,
        )
        self.text = text

    @property
    def capacity(self):
        """Alias of self.count_inner."""
        return self.count_inner

    @property
    def util_rate_with_distances(self):
        return self.inner.A * self.count_inner / self.A_with_distances


class VisuDataDirectDoubleRow(
    RectangleWithDistances, RectangleWithRotatedDoubleRowInner
):
    """Representation of a double row direct area.

    text: [str] to draw in the center of the rectangle
    """

    conflict_category = 4

    def __init__(
        self,
        m=LENGTH_SAFE,
        n=WIDTH_SAFE,
        capacity=1,
        x=0,
        y=0,
        d_l=DIRECT_DISTANCE_A,
        d_b=DIRECT_DISTANCE_B,
        d_r=DIRECT_DISTANCE_A,
        d_t=DIRECT_DISTANCE_B,
        text="",
    ):
        super(VisuDataDirectDoubleRow, self).__init__(
            m=m, n=n, count_inner=capacity, x=x, y=y, d_l=d_l, d_b=d_b, d_r=d_r, d_t=d_t
        )
        self.text = text

    @property
    def capacity(self):
        """Alias of self.count_inner."""
        return self.count_inner

    @property
    def util_rate_with_distances(self):
        return self.inner_left.A * self.count_inner / self.A_with_distances


def draw_rectangles(rectangles, draw_distances=True):
    """rectangles: [iterable] of Rectangle oder subclass objects."""
    fig = plt.gcf()
    ax = plt.gca()

    [p.remove() for p in reversed(ax.patches)]
    for r in rectangles:
        if isinstance(r, RectangleWithInner) or isinstance(
            r, RectangleWithRotatableInner
        ):
            ax.add_patch(
                patches.Rectangle(
                    (r.x, r.y), r.a, r.b, fill=r.fill, linestyle=r.linestyle
                )
            )
            # Inner rectangles
            for i in range(r.count_inner):
                ax.add_patch(
                    patches.Rectangle(
                        (r.x_inner(i), r.y_inner(i)),
                        r.inner.a,
                        r.inner.b,
                        r.inner.angle,
                        color=r.inner.color,
                        alpha=0.4,
                        fill=r.inner.fill,
                        linestyle=r.inner.linestyle,
                    )
                )

        if isinstance(r, RectangleWithRotatedDoubleRowInner):
            ax.add_patch(
                patches.Rectangle(
                    (r.x, r.y), r.a, r.b, fill=r.fill, linestyle=r.linestyle
                )
            )
            # Left inner rectangles
            for i in range(0, r.count_inner, 2):
                ax.add_patch(
                    patches.Rectangle(
                        (r.x_inner(i), r.y_inner(i)),
                        r.inner_left.a,
                        r.inner_left.b,
                        r.inner_left.angle,
                        color=r.inner_left.color,
                        alpha=0.4,
                        fill=r.inner_left.fill,
                        linestyle=r.inner_left.linestyle,
                    )
                )
            # Right inner rectangles
            for i in range(1, r.count_inner, 2):
                ax.add_patch(
                    patches.Rectangle(
                        (r.x_inner(i), r.y_inner(i)),
                        r.inner_right.a,
                        r.inner_right.b,
                        r.inner_right.angle,
                        color="red",
                        alpha=0.4,
                        fill=r.inner_right.fill,
                        linestyle=r.inner_right.linestyle,
                    )
                )

        # Vanilla case
        elif isinstance(r, Rectangle):
            ax.add_patch(
                patches.Rectangle(
                    (r.x, r.y),
                    r.a,
                    r.b,
                    r.angle,
                    color=r.color,
                    fill=r.fill,
                    alpha=r.alpha,
                    linestyle=r.linestyle,
                )
            )

        # Buffer distances
        if draw_distances and isinstance(r, RectangleWithDistances):
            for distance_rec in (
                r.distance_left,
                r.distance_bottom,
                r.distance_right,
                r.distance_top,
            ):
                if distance_rec.A > 0:
                    ax.add_patch(
                        patches.Rectangle(
                            (distance_rec.x, distance_rec.y),
                            distance_rec.a,
                            distance_rec.b,
                            fill=distance_rec.fill,
                            color=distance_rec.color,
                            alpha=distance_rec.alpha,
                            linestyle=distance_rec.linestyle,
                        )
                    )

        # Distances of a BinWithDistances
        if draw_distances and isinstance(r, BinWithDistances):
            for distance_rec in (
                r.distance_left_inner,
                r.distance_bottom_inner,
                r.distance_right_inner,
                r.distance_top_inner,
            ):
                if distance_rec.A > 0:
                    ax.add_patch(
                        patches.Rectangle(
                            (distance_rec.x, distance_rec.y),
                            distance_rec.a,
                            distance_rec.b,
                            fill=distance_rec.fill,
                            color=distance_rec.color,
                            alpha=distance_rec.alpha,
                            linestyle=distance_rec.linestyle,
                        )
                    )

        # Draw text if there is any
        if hasattr(r, "text") and r.text:
            box_props = dict(boxstyle="round", facecolor="white", alpha=0.5)
            ax.text(
                r.x_center, r.y_center, r.text, ha="center", va="center", bbox=box_props
            )

    plt.draw()


def draw_rectangles_newplot(
    rectangles, xlim=(0, 100), ylim=(0, 100), draw_distances=True
):
    """Initialize a new plot and draw rectangles.

    rectangles: sequence of Rectangle oder subclass objects
    xlim: [tuple] of x min and max
    ylim: [tuple] of y min and max
    """
    fig, ax = plt.subplots()
    draw_rectangles(rectangles, draw_distances)
    ax.set_aspect("equal")
    plt.xlim(*xlim)
    plt.ylim(*ylim)
    plt.show()
    return fig, ax


def packem(n=10):
    """Demo for packing without distances."""
    bin = Bin(150, 150)
    for ii in range(n):
        bin.items.append(VisuDataDirectSingleRow(capacity=randint(1, 5)))
        bin.items.append(VisuDataLine(capacity=randint(2, 4)))

    bin.pack()
    return bin


def packem_with_distances(n=5):
    """Demo for packing with distances."""
    bin = BinWithDistances(150, 150)
    for ii in range(n):
        bin.items.append(VisuDataDirectSingleRow(capacity=randint(1, 5)))
        bin.items.append(VisuDataLine(capacity=randint(2, 4)))
        bin.items.append(VisuDataDirectDoubleRow(capacity=randint(2, 5)))

    bin.pack()
    return bin


class Progressprinter:
    def __init__(self, until, interval=0.1):
        self.until = until
        self.last = 0
        self.interval = interval
        self.next = 0

    def notify(self, now):
        if self.next <= now:
            print("Progress: %d (%d %%)" % (now, now / self.until * 100))
            self.next += self.until * self.interval


def test_packing(tries=250, nmax=30):
    """Test for packing without distances."""
    propri = Progressprinter(tries)
    for i in range(tries):
        propri.notify(i)

        packem(randint(1, nmax))
    print("No error encountered in %d tries." % tries)


def test_packing_with_distances(tries=250, nmax=15):
    """Test for packing with distances."""
    propri = Progressprinter(tries)
    for i in range(tries):
        propri.notify(i)

        packem_with_distances(randint(1, nmax))
    print("No error encountered in %d tries." % tries)


lang_dict = {"Step": "Schritt", "av": "V", "no": "Nr."}


def tl(text):
    """Translate *text* using lang_dict."""
    if language == "en":
        return text
    else:
        return lang_dict[text]


if __name__ == "__main__":
    pass
