import { alpha } from "./alpha";
import { beta } from "./beta";
import { gamma } from "./gamma";

type User = {
  id: string;
};

const LIMIT = 10;
const OFFSET = 2;

function untouchedOne() {
  return alpha;
}

function untouchedTwo() {
  return beta;
}

class Presenter {
  renderHeader() {
    return gamma;
  }

  renderBody() {
    return LIMIT;
  }

  renderFooter() {
    return OFFSET;
  }
}

function untouchedTail() {
  return "tail";
}
